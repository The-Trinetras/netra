"""Session persistence interface.

PostgreSQL is authoritative for session state (data-ownership.md). The
account–session security binding moved to the Identity repository
(netra_api.identity.repository): Identity decides who may act on a session;
this repository only stores what that session's state is.

The central operation is ``commit``: it applies an expected-version state
write AND records the request's replay entry in one atomic unit. Writing
them separately would let a crash leave a moved position with no replay
record (a retry would move it again) or a replay record for a write that
never happened (a retry would report success for nothing).
"""

from __future__ import annotations

from typing import Any, Optional, Protocol
from uuid import UUID

from netra_api.platform.idempotency import RecordedRequest
from netra_api.session.state import SessionState


class RequestAlreadyRecordedError(Exception):
    """Raised by commit when request_id was recorded concurrently.

    Two identical retransmissions can pass the initial replay lookup at the
    same time. The unique (account_id, request_id) constraint lets exactly
    one commit; the other receives the winner's entry and resolves it as a
    replay or a conflict instead of applying a second effect.
    """

    def __init__(self, recorded: RecordedRequest) -> None:
        self.recorded = recorded
        super().__init__("request_id already recorded")


class SessionRepository(Protocol):
    """Typed contract for reading and atomically mutating session state."""

    async def get(self, session_id: UUID) -> Optional[SessionState]:
        ...

    async def create(self, state: SessionState) -> SessionState:
        """Insert a brand-new session row. Raises if session_id exists."""
        ...

    async def get_recorded(self, account_id: UUID, request_id: UUID) -> Optional[RecordedRequest]:
        ...

    async def commit(
        self,
        *,
        account_id: UUID,
        session_id: UUID,
        request_id: Optional[UUID],
        payload_fingerprint: Optional[str],
        result: Optional[dict[str, Any]],
        expected_version: int,
        new_state: Optional[SessionState],
    ) -> Optional[SessionState]:
        """Atomically record request_id and, when new_state is given, write it.

        request_id is None only for server-originated merges that carry no
        client replay identity (playback acknowledgements, which the
        contract gives no expected version); then only the state is written.

        - Raises RequestAlreadyRecordedError if (account_id, request_id)
          already has an entry; nothing is written.
        - When new_state is given, it must carry session_version ==
          expected_version + 1; raises SessionVersionConflictError (and
          writes nothing) if the stored version is not expected_version.
        - When new_state is None only the replay entry is written (a
          read-only or no-change command), and the session version does not
          advance: duplicate or no-op effects never move the version.
        """
        ...
