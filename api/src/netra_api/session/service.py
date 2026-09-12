"""Session service: the only path through which callers read or mutate session state.

Wraps a SessionRepository with the idempotency and version-check rules
from CLAUDE.md "Session rules": duplicate request IDs must not apply the
same mutation twice, and navigation mutations must use expected session
versions.
"""

from __future__ import annotations

from uuid import UUID

from netra_api.platform.auth_context import AuthContext
from netra_api.platform.idempotency import (
    IdempotencyStore,
    check_expected_version,
    replay_recorded_result,
)
from netra_api.session.commands import NavigationCommandRequest, NavigationCommandResult
from netra_api.session.repository import SessionRepository
from netra_api.session.state import SessionState


class SessionService:
    """Application-facing session operations. No SQL and no LLM calls here."""

    def __init__(self, repository: SessionRepository, idempotency_store: IdempotencyStore) -> None:
        self._repository = repository
        self._idempotency_store = idempotency_store

    def get_state(self, auth: AuthContext, session_id: UUID) -> SessionState:
        auth.assert_owns_session(session_id)
        return self._repository.get(session_id)

    def apply_navigation_command(
        self,
        auth: AuthContext,
        session_id: UUID,
        request_id: UUID,
        command: NavigationCommandRequest,
    ) -> NavigationCommandResult:
        """Apply a deterministic navigation command. Never routes through an LLM.

        The ordering here is the contract every dispatch path must keep:

        1. Account/session ownership, so an unauthorized caller stops here.
        2. Replay check. A duplicate request_id returns the result the
           first delivery produced, without re-applying anything and
           without advancing the session version again (coordinator.md:
           "Return the prior result for a duplicate operation").
        3. Expected-version check, so a stale client conflicts instead of
           overwriting a concurrent mutation from another device.

        Only then may the command be applied.

        TODO: dispatch to a concrete NavigationCommandHandler
        (netra_api.session.commands.NavigationCommandHandler), then
        persist via SessionRepository.save and record the outcome with
        IdempotencyStore.record_result in the same transaction. Both
        require reading-position persistence, which does not exist yet.
        """

        auth.assert_owns_session(session_id)

        replayed = replay_recorded_result(
            self._idempotency_store, request_id, NavigationCommandResult
        )
        if replayed is not None:
            return replayed

        session = self._repository.get(session_id)
        check_expected_version(command.expected_session_version, session.session_version)
        raise NotImplementedError("navigation command dispatch is not yet implemented")
