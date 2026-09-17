"""Session service: the only path through which callers read or mutate session state.

Ordering every client-originated mutation keeps (message-flow.md flow 2,
tightened 2026-09-12):

1. Account/session access — AuthContext was issued by IdentityService, and
   assert_owns_session rejects a context minted for another session.
2. Replay resolution BEFORE any version check: an identical retransmission
   replays its committed result unconditionally, even if its
   expected_session_version is now stale; the same request_id with a
   different payload fails closed (REQUEST_ID_CONFLICT).
3. Only for a genuinely new request_id: the expected-version check.
4. Decide the effect, then commit state and replay record atomically. A
   concurrent identical request that loses the commit race replays the
   winner's result; a concurrent different request conflicts on version.

Server-originated merges (playback acknowledgements, a turn's final session
delta) re-read the latest state and retry a bounded number of times on a
version race, because their validity was already checked when accepted and
the contract gives acknowledgements no expected version.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional
from uuid import UUID, uuid4

from pydantic import RootModel

from netra_api.platform.auth_context import AuthContext
from netra_api.platform.errors import (
    InvalidRequestError,
    ResourceUnavailableError,
    SessionVersionConflictError,
    StaleRequestError,
)
from netra_api.platform.idempotency import check_expected_version, resolve_recorded
from netra_api.session.modes import ConnectionState, InteractionMode
from netra_api.session.reading import ReadingAccess
from netra_api.session.repository import RequestAlreadyRecordedError, SessionRepository
from netra_api.session.result_sets import ResultSet, ResultSetItem, ResultSetRepository
from netra_api.session.state import (
    AccountContext,
    PlaybackAcknowledgement,
    ReadingPosition,
    ResultSetRef,
    SessionState,
)
from netra_api.speech.playback_metadata import DeliveredSentence

Clock = Callable[[], datetime]
Render = Callable[[SessionState], dict[str, Any]]
Decide = Callable[[SessionState], Awaitable[tuple[Optional[SessionState], Render]]]
Merge = Callable[[SessionState], Awaitable[Optional[SessionState]]]

MAX_MERGE_ATTEMPTS = 3
"""Bounded optimistic retries for server-originated merges. An
implementation bound for a compare-and-swap race, not product policy."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _durable_view(state: SessionState) -> dict[str, Any]:
    """Fields whose change constitutes an accepted session change."""

    return state.model_dump(exclude={"session_version", "updated_at", "connection_state"})


@dataclass(frozen=True)
class RequestOutcome:
    result: dict[str, Any]
    state: SessionState
    replayed: bool
    changed: bool


class SessionService:
    """Application-facing session operations. No SQL and no model calls here."""

    def __init__(
        self,
        repository: SessionRepository,
        *,
        reading: Optional[ReadingAccess] = None,
        result_sets: Optional[ResultSetRepository] = None,
        result_set_ttl: Optional[timedelta] = None,
        clock: Clock = _utcnow,
    ) -> None:
        self._repository = repository
        self._reading = reading
        self._result_sets = result_sets
        self._result_set_ttl = result_set_ttl
        self._clock = clock

    # -- reads ---------------------------------------------------------------

    async def get_state(self, auth: AuthContext, session_id: Optional[UUID] = None) -> SessionState:
        session_id = session_id or auth.session_id
        auth.assert_owns_session(session_id)
        state = await self._repository.get(session_id)
        if state is None:
            raise ResourceUnavailableError("session state is unavailable")
        auth.assert_owns_account(state.account.account_id)
        return state

    async def recorded_result(
        self, auth: AuthContext, request_id: UUID, payload_fingerprint: str
    ) -> Optional[dict[str, Any]]:
        """Replay lookup only: prior result, None, or IdempotencyConflictError."""

        recorded = await self._repository.get_recorded(auth.account_id, request_id)
        replay = resolve_recorded(recorded, payload_fingerprint, _ResultEnvelope)
        return replay.root if replay is not None else None

    # -- creation --------------------------------------------------------------

    async def create_session(self, auth: AuthContext) -> SessionState:
        """Initialise canonical state for a session Identity has just bound.

        Requires an AuthContext, i.e. the binding already exists; the Session
        service never decides who owns a session.
        """

        now = self._clock()
        state = SessionState(
            session_id=auth.session_id,
            account=AccountContext(account_id=auth.account_id),
            connection_state=ConnectionState.CONNECTED,
            interaction_mode=InteractionMode.IDLE,
            session_version=0,
            updated_at=now,
        )
        return await self._repository.create(state)

    # -- client-originated requests -------------------------------------------

    async def handle_request(
        self,
        auth: AuthContext,
        *,
        request_id: UUID,
        payload_fingerprint: str,
        expected_version: Optional[int],
        decide: Decide,
    ) -> RequestOutcome:
        auth.assert_owns_session(auth.session_id)

        prior = await self.recorded_result(auth, request_id, payload_fingerprint)
        if prior is not None:
            return RequestOutcome(result=prior, state=await self.get_state(auth), replayed=True, changed=False)

        state = await self.get_state(auth)
        if expected_version is not None:
            check_expected_version(expected_version, state.session_version)

        proposed, render = await decide(state)
        final, changed = self._advance(state, proposed)
        result = render(final)
        try:
            await self._repository.commit(
                account_id=auth.account_id,
                session_id=state.session_id,
                request_id=request_id,
                payload_fingerprint=payload_fingerprint,
                result=result,
                expected_version=state.session_version,
                new_state=final if changed else None,
            )
        except RequestAlreadyRecordedError as raced:
            replay = resolve_recorded(raced.recorded, payload_fingerprint, _ResultEnvelope)
            return RequestOutcome(result=replay.root, state=await self.get_state(auth), replayed=True, changed=False)
        return RequestOutcome(result=result, state=final, replayed=False, changed=changed)

    async def commit_turn(
        self,
        auth: AuthContext,
        *,
        request_id: UUID,
        payload_fingerprint: str,
        merge: Merge,
        render: Render,
    ) -> RequestOutcome:
        """Record a finished turn's result with its session delta merged onto the latest state.

        The turn's expected_session_version was checked when it was accepted.
        Acknowledgements may legitimately advance the version while the turn
        runs, so the delta is re-applied to the latest state on a race.
        """

        for _ in range(MAX_MERGE_ATTEMPTS):
            state = await self.get_state(auth)
            final, changed = self._advance(state, await merge(state))
            result = render(final)
            try:
                await self._repository.commit(
                    account_id=auth.account_id,
                    session_id=state.session_id,
                    request_id=request_id,
                    payload_fingerprint=payload_fingerprint,
                    result=result,
                    expected_version=state.session_version,
                    new_state=final if changed else None,
                )
                return RequestOutcome(result=result, state=final, replayed=False, changed=changed)
            except SessionVersionConflictError:
                continue
            except RequestAlreadyRecordedError as raced:
                replay = resolve_recorded(raced.recorded, payload_fingerprint, _ResultEnvelope)
                return RequestOutcome(result=replay.root, state=await self.get_state(auth), replayed=True, changed=False)
        raise ResourceUnavailableError("session changed repeatedly while committing the turn")

    async def merge_internal(self, auth: AuthContext, merge: Merge) -> tuple[SessionState, bool]:
        """State-only merge with no replay record (e.g. playback acknowledgement)."""

        for _ in range(MAX_MERGE_ATTEMPTS):
            state = await self.get_state(auth)
            final, changed = self._advance(state, await merge(state))
            if not changed:
                return state, False
            try:
                await self._repository.commit(
                    account_id=auth.account_id,
                    session_id=state.session_id,
                    request_id=None,
                    payload_fingerprint=None,
                    result=None,
                    expected_version=state.session_version,
                    new_state=final,
                )
                return final, True
            except SessionVersionConflictError:
                continue
        raise ResourceUnavailableError("session changed repeatedly while applying the update")

    # -- specific operations -----------------------------------------------------

    async def apply_playback_ack(
        self, auth: AuthContext, sentence: DeliveredSentence, *, generation_id: str, status: str, played_ms: Optional[int]
    ) -> tuple[SessionState, bool]:
        """Persist actual playback progress for an eligible delivered sentence.

        Only ``completed`` is persisted (started/progress stay in the
        generation registry; progress cadence is an open M1/M5 decision).
        A completed source-reading sentence moves the reading position to
        exactly that sentence; generated content never moves it. A sentence
        from a source version other than the pinned one is stale.
        """

        if status != "completed":
            return await self.get_state(auth), False

        async def merge(state: SessionState) -> Optional[SessionState]:
            ack = PlaybackAcknowledgement(
                generation_id=generation_id,
                segment_id=sentence.segment_id,
                sentence_id=sentence.sentence_id,
                status=status,
                played_ms=played_ms,
            )
            updates: dict[str, Any] = {"last_playback_ack": ack}
            if sentence.origin == "source_reading":
                if sentence.source_version_id != state.reading_position.source_version_id:
                    raise StaleRequestError("acknowledged sentence is not from the pinned source version")
                updates["reading_position"] = ReadingPosition(
                    source_version_id=sentence.source_version_id,
                    current_block_id=sentence.block_id,
                    current_sentence_id=sentence.sentence_id,
                )
            return state.model_copy(update=updates)

        return await self.merge_internal(auth, merge)

    async def pin_source(self, state: SessionState, auth: AuthContext, source_version_id: str) -> SessionState:
        """Proposed next state for an EXPLICIT source switch (decide callback helper).

        Authorizes the version, starts at its first reading position, and
        clears return/undo positions and the result-set reference that
        belonged to the previous source. Re-selecting the already pinned
        version keeps the current position. A newly activated version of the
        same source never moves a session implicitly; only this explicit call
        changes the pin.
        """

        if self._reading is None:
            raise ResourceUnavailableError("reading services are not registered")
        await self._reading.assert_source_access(auth, source_version_id)
        if state.reading_position.source_version_id == source_version_id:
            return state.model_copy(update={"interaction_mode": InteractionMode.READING})
        first = await self._reading.first(source_version_id)
        return state.model_copy(
            update={
                "reading_position": first,
                "interaction_mode": InteractionMode.READING,
                "reading_return_position": None,
                "undo_jump_position": None,
                "last_result_set": None,
                "last_playback_ack": None,
            }
        )

    async def record_result_set(
        self, auth: AuthContext, source_version_id: str, items: list[ResultSetItem]
    ) -> ResultSetRef:
        """Store an ordered result list, then point the session at it.

        The row is written before the session references it, so a snapshot
        never names a list still being formed. Retention is an unapproved
        product value: without a configured TTL this fails explicitly and
        no reference is stored.
        """

        if self._result_sets is None or self._result_set_ttl is None:
            raise ResourceUnavailableError("result-set retention is not configured")
        now = self._clock()
        result_set = ResultSet(
            result_set_id=uuid4(),
            session_id=auth.session_id,
            source_version_id=source_version_id,
            created_at=now,
            expires_at=now + self._result_set_ttl,
            items=[item.model_copy(update={"ordinal": index}) for index, item in enumerate(items)],
        )
        await self._result_sets.create(result_set)
        reference = ResultSetRef(result_set_id=result_set.result_set_id, created_at=now)

        async def merge(state: SessionState) -> Optional[SessionState]:
            return state.model_copy(update={"last_result_set": reference})

        await self.merge_internal(auth, merge)
        return reference

    async def resolve_result_item(self, auth: AuthContext, ordinal: int) -> ResultSetItem:
        """Resolve "the Nth one" against exactly the list last presented.

        Returns only an evidence reference; the caller must still resolve it
        through the evidence authorization path.
        """

        state = await self.get_state(auth)
        if state.last_result_set is None or self._result_sets is None:
            raise StaleRequestError("there is no current result list")
        result_set = await self._result_sets.get(auth.session_id, state.last_result_set.result_set_id, self._clock())
        if result_set is None:
            raise StaleRequestError("the result list has expired")
        if result_set.source_version_id != state.reading_position.source_version_id:
            raise StaleRequestError("the result list belongs to a different source version")
        if ordinal < 0 or ordinal >= len(result_set.items):
            raise InvalidRequestError("no result at that position", field="ordinal")
        return result_set.items[ordinal]

    # -- internals ---------------------------------------------------------------

    def _advance(self, state: SessionState, proposed: Optional[SessionState]) -> tuple[SessionState, bool]:
        if proposed is None or _durable_view(proposed) == _durable_view(state):
            return state, False
        if proposed.session_id != state.session_id or proposed.account != state.account:
            raise InvalidRequestError("a session mutation cannot change identity fields")
        return (
            proposed.model_copy(update={"session_version": state.session_version + 1, "updated_at": self._clock()}),
            True,
        )


class _ResultEnvelope(RootModel[dict[str, Any]]):
    """Recorded results are opaque rendered payload dicts."""
