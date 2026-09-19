"""SessionService over the real PostgreSQL repository (disposable database).

The in-memory repository checks the replay record before the version. These
tests prove the PostgreSQL repository gives the same answers under genuine
concurrency: two transactions racing on one session row and one request id.
"""

import asyncio
import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select

from netra_api.platform.auth_context import AuthContext
from netra_api.platform.database import create_engine
from netra_api.platform.errors import IdempotencyConflictError, SessionVersionConflictError
from netra_api.session.modes import ConnectionState, InteractionMode
from netra_api.session.postgres import PostgresSessionRepository, session_request_records, sessions
from netra_api.session.service import SessionService
from netra_api.session.state import AccountContext, ReadingPosition, SessionState

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 18, tzinfo=timezone.utc)


@pytest.fixture
async def engine():
    url = os.environ.get("NETRA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("NETRA_TEST_DATABASE_URL (a disposable local database) is not set")
    engine = create_engine(url)
    yield engine
    await engine.dispose()


@pytest.fixture
async def seeded(engine):
    repository = PostgresSessionRepository(engine)
    account_id, session_id = uuid4(), uuid4()
    await repository.create(SessionState(
        session_id=session_id, account=AccountContext(account_id=account_id),
        connection_state=ConnectionState.CONNECTED, interaction_mode=InteractionMode.READING,
        reading_position=ReadingPosition(source_version_id="v1", current_block_id="b1", current_sentence_id="s1"),
        session_version=5, updated_at=NOW))
    auth = AuthContext(account_id=account_id, session_id=session_id, request_id=uuid4(), issued_at=NOW)
    yield SessionService(repository), repository, auth
    async with engine.begin() as connection:
        await connection.execute(delete(session_request_records).where(session_request_records.c.session_id == session_id))
        await connection.execute(delete(sessions).where(sessions.c.session_id == session_id))


def _move(block, barrier=None):
    async def decide(state):
        if barrier is not None:
            await barrier.wait()  # both requests pass replay + version checks before either commits
        return (state.model_copy(update={"reading_position": state.reading_position.model_copy(update={"current_block_id": block})}),
                lambda final: {"version": final.session_version, "block": block})
    return decide


class _Barrier:
    def __init__(self, parties):
        self._parties, self._arrived, self._event = parties, 0, asyncio.Event()

    async def wait(self):
        self._arrived += 1
        if self._arrived >= self._parties:
            self._event.set()
        await self._event.wait()


async def _records(engine, auth):
    async with engine.connect() as connection:
        return (await connection.execute(select(func.count()).select_from(session_request_records).where(
            session_request_records.c.account_id == auth.account_id))).scalar_one()


async def test_concurrent_identical_retransmissions_commit_once_and_both_get_the_result(engine, seeded):
    service, repository, auth = seeded
    request_id, barrier = uuid4(), _Barrier(2)
    outcomes = await asyncio.gather(*[
        service.handle_request(auth, request_id=request_id, payload_fingerprint="fp", expected_version=5,
                               decide=_move("b2", barrier))
        for _ in range(2)])
    assert sorted(o.replayed for o in outcomes) == [False, True]
    assert [o.result for o in outcomes] == [{"version": 6, "block": "b2"}] * 2
    state = await repository.get(auth.session_id)
    assert state.session_version == 6 and state.reading_position.current_block_id == "b2"
    assert await _records(engine, auth) == 1


async def test_concurrent_different_requests_on_one_version_move_once(engine, seeded):
    service, repository, auth = seeded
    barrier = _Barrier(2)
    outcomes = await asyncio.gather(*[
        service.handle_request(auth, request_id=uuid4(), payload_fingerprint=f"fp-{block}", expected_version=5,
                               decide=_move(block, barrier))
        for block in ("b2", "b3")], return_exceptions=True)
    conflicts = [o for o in outcomes if isinstance(o, SessionVersionConflictError)]
    winners = [o for o in outcomes if not isinstance(o, BaseException)]
    assert len(conflicts) == 1 and len(winners) == 1
    assert conflicts[0].actual_version == 6
    state = await repository.get(auth.session_id)
    assert state.session_version == 6 and state.reading_position.current_block_id == winners[0].result["block"]
    assert await _records(engine, auth) == 1


async def test_replay_ignores_a_stale_version_and_conflicting_reuse_fails_closed(engine, seeded):
    service, repository, auth = seeded
    request_id = uuid4()
    first = await service.handle_request(auth, request_id=request_id, payload_fingerprint="fp", expected_version=5, decide=_move("b2"))
    await service.handle_request(auth, request_id=uuid4(), payload_fingerprint="other", expected_version=6, decide=_move("b3"))
    replay = await service.handle_request(auth, request_id=request_id, payload_fingerprint="fp", expected_version=5, decide=_move("b9"))
    assert replay.replayed and replay.result == first.result
    with pytest.raises(IdempotencyConflictError):
        await service.handle_request(auth, request_id=request_id, payload_fingerprint="different", expected_version=7, decide=_move("b9"))
    state = await repository.get(auth.session_id)
    assert state.session_version == 7 and state.reading_position.current_block_id == "b3"


async def test_a_failed_commit_leaves_neither_state_nor_replay_record(engine, seeded):
    service, repository, auth = seeded
    request_id = uuid4()
    with pytest.raises(SessionVersionConflictError):
        await repository.commit(account_id=auth.account_id, session_id=auth.session_id, request_id=request_id,
                                payload_fingerprint="fp", result={"x": 1}, expected_version=4,
                                new_state=(await repository.get(auth.session_id)).model_copy(update={"session_version": 5}))
    assert await repository.get_recorded(auth.account_id, request_id) is None
    assert (await repository.get(auth.session_id)).session_version == 5


async def test_concurrent_server_merges_both_apply_without_losing_either(engine, seeded):
    service, repository, auth = seeded

    def merge(block_field, value):
        async def apply(state):
            await asyncio.sleep(0)
            return state.model_copy(update={"reading_position": state.reading_position.model_copy(update={block_field: value})})
        return apply

    await asyncio.gather(service.merge_internal(auth, merge("current_block_id", "b7")),
                         service.merge_internal(auth, merge("current_sentence_id", "s9")))
    state = await repository.get(auth.session_id)
    assert state.session_version == 7
    assert (state.reading_position.current_block_id, state.reading_position.current_sentence_id) == ("b7", "s9")
