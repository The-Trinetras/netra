"""SessionService: pinning, result sets, server-side merges and version semantics.

Uses the non-durable in-memory repository; persistence itself is not proven here.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from netra_api.platform.auth_context import AuthContext
from netra_api.platform.errors import (
    AuthorizationError,
    IdempotencyConflictError,
    ResourceUnavailableError,
    SessionVersionConflictError,
    StaleRequestError,
)
from netra_api.session.memory import InMemoryResultSetRepository, InMemorySessionRepository
from netra_api.session.modes import ConnectionState, InteractionMode
from netra_api.session.result_sets import ResultSetItem
from netra_api.session.service import SessionService
from netra_api.session.state import AccountContext, ReadingPosition, SessionState
from netra_api.speech.playback_metadata import DeliveredSentence

NOW = datetime(2026, 9, 17, tzinfo=timezone.utc)


class _Reading:
    def __init__(self, owned):
        self.owned = owned

    async def assert_source_access(self, auth, source_version_id):
        if source_version_id not in self.owned:
            raise AuthorizationError("not owned")

    async def first(self, source_version_id):
        return ReadingPosition(source_version_id=source_version_id, current_block_id="b-first", current_sentence_id="s-first")


async def _service(**kwargs):
    repository = InMemorySessionRepository()
    account_id, session_id = uuid4(), uuid4()
    state = SessionState(
        session_id=session_id,
        account=AccountContext(account_id=account_id),
        connection_state=ConnectionState.CONNECTED,
        interaction_mode=InteractionMode.READING,
        reading_position=ReadingPosition(source_version_id="v1", current_block_id="b12", current_sentence_id="s3"),
        session_version=5,
        updated_at=NOW,
    )
    await repository.create(state)
    auth = AuthContext(account_id=account_id, session_id=session_id, request_id=uuid4(), issued_at=NOW)
    return SessionService(repository, clock=lambda: NOW, **kwargs), repository, auth


def _move_to(block):
    async def decide(state):
        return state.model_copy(update={"reading_position": state.reading_position.model_copy(update={"current_block_id": block})}), lambda final: {"version": final.session_version}

    return decide


async def test_context_for_another_session_is_refused():
    service, _, auth = await _service()
    with pytest.raises(AuthorizationError):
        await service.get_state(auth, uuid4())


async def test_replay_precedes_version_check_and_conflicting_reuse_fails_closed():
    service, repository, auth = await _service()
    request_id = uuid4()
    first = await service.handle_request(auth, request_id=request_id, payload_fingerprint="fp-a", expected_version=5, decide=_move_to("b13"))
    assert first.result == {"version": 6} and not first.replayed

    replay = await service.handle_request(auth, request_id=request_id, payload_fingerprint="fp-a", expected_version=5, decide=_move_to("b99"))
    assert replay.replayed and replay.result == {"version": 6}
    assert (await repository.get(auth.session_id)).reading_position.current_block_id == "b13"

    with pytest.raises(IdempotencyConflictError):
        await service.handle_request(auth, request_id=request_id, payload_fingerprint="fp-b", expected_version=6, decide=_move_to("b99"))
    with pytest.raises(SessionVersionConflictError):
        await service.handle_request(auth, request_id=uuid4(), payload_fingerprint="fp-c", expected_version=5, decide=_move_to("b99"))


async def test_no_change_request_records_replay_without_advancing_version():
    service, repository, auth = await _service()

    async def unchanged(state):
        return state, lambda final: {"version": final.session_version}

    outcome = await service.handle_request(auth, request_id=uuid4(), payload_fingerprint="fp", expected_version=5, decide=unchanged)
    assert outcome.changed is False
    assert (await repository.get(auth.session_id)).session_version == 5


async def test_turn_commit_merges_onto_latest_state_after_a_concurrent_change():
    service, repository, auth = await _service()
    raced = False

    async def merge(latest):
        nonlocal raced
        if not raced:
            raced = True
            await service.merge_internal(auth, _ack_merge("s1"))
        return latest.model_copy(update={"interaction_mode": InteractionMode.TUTOR_LESSON})

    outcome = await service.commit_turn(auth, request_id=uuid4(), payload_fingerprint="turn", merge=merge, render=lambda s: {"v": s.session_version})
    state = await repository.get(auth.session_id)
    assert outcome.result == {"v": 7}
    assert state.interaction_mode == InteractionMode.TUTOR_LESSON
    assert state.reading_position.current_sentence_id == "s1"  # the concurrent change survived


def _ack_merge(sentence):
    async def merge(state):
        return state.model_copy(update={"reading_position": state.reading_position.model_copy(update={"current_sentence_id": sentence})})

    return merge


async def test_completed_ack_from_another_source_version_is_stale():
    service, repository, auth = await _service()
    sentence = DeliveredSentence(segment_id="b1", sentence_id="s1", origin="source_reading", source_version_id="v2", block_id="b1")
    with pytest.raises(StaleRequestError):
        await service.apply_playback_ack(auth, sentence, generation_id="g", status="completed", played_ms=None)
    assert (await repository.get(auth.session_id)).session_version == 5


async def test_generated_content_ack_records_playback_but_never_moves_reading():
    service, repository, auth = await _service()
    sentence = DeliveredSentence(segment_id="seg-0", sentence_id="gen-s", origin="generated")
    state, changed = await service.apply_playback_ack(auth, sentence, generation_id="g", status="completed", played_ms=1200)
    assert changed and state.last_playback_ack.sentence_id == "gen-s"
    assert state.reading_position.current_sentence_id == "s3"


async def test_explicit_source_switch_is_the_only_way_the_pin_changes():
    service, _, auth = await _service(reading=_Reading({"v1", "v2"}))
    state = await service.get_state(auth)
    same = await service.pin_source(state, auth, "v1")
    assert same.reading_position == state.reading_position
    switched = await service.pin_source(state, auth, "v2")
    assert switched.reading_position.source_version_id == "v2" and switched.undo_jump_position is None
    with pytest.raises(AuthorizationError):
        await service.pin_source(state, auth, "someone-elses")


async def test_result_sets_need_configured_retention_and_resolve_stable_ordinals():
    service, _, auth = await _service()
    with pytest.raises(ResourceUnavailableError):
        await service.record_result_set(auth, "v1", [ResultSetItem(ordinal=0, evidence_id="a")])

    service, _, auth = await _service(result_sets=InMemoryResultSetRepository(), result_set_ttl=timedelta(minutes=5))
    items = [ResultSetItem(ordinal=9, evidence_id=f"ev-{i}", label=f"result {i}") for i in range(3)]
    await service.record_result_set(auth, "v1", items)
    assert (await service.resolve_result_item(auth, 2)).evidence_id == "ev-2"
    with pytest.raises(Exception):
        await service.resolve_result_item(auth, 3)


async def test_expired_result_set_is_stale_not_silently_resolved():
    clock = [NOW]
    repository = InMemorySessionRepository()
    service, repository, auth = await _service(result_sets=InMemoryResultSetRepository(), result_set_ttl=timedelta(minutes=5))
    await service.record_result_set(auth, "v1", [ResultSetItem(ordinal=0, evidence_id="ev")])
    service._clock = lambda: NOW + timedelta(minutes=6)
    with pytest.raises(StaleRequestError):
        await service.resolve_result_item(auth, 0)


async def test_concurrent_identical_requests_commit_once():
    service, repository, auth = await _service()
    request_id = uuid4()
    results = await asyncio.gather(
        *[service.handle_request(auth, request_id=request_id, payload_fingerprint="same", expected_version=5, decide=_move_to("b13")) for _ in range(5)]
    )
    assert sum(1 for r in results if not r.replayed) == 1
    assert (await repository.get(auth.session_id)).session_version == 6
