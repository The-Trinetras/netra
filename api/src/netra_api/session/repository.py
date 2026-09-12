"""Session persistence interface.

PostgreSQL is the authoritative store for session state (CLAUDE.md "Data
authority"). This module only defines the interface SessionService
depends on so no agent code ever opens a database connection directly.
A concrete PostgreSQL-backed implementation is added alongside the
migration for the sessions table, not here.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from netra_api.session.state import SessionState


class SessionRepository(Protocol):
    """Typed contract for reading/writing session state."""

    def get_owner_account_id(self, session_id: UUID) -> UUID:
        """Return the account PostgreSQL records as owning session_id.

        This is the authority behind
        netra_api.platform.auth_context.AuthContext's session binding:
        netra_api.identity.service.IdentityService.resolve_auth_context
        calls it before issuing a context, so a client-supplied
        session_id never becomes authorization on its own (CLAUDE.md
        "Data authority and security").

        Raises netra_api.platform.errors.AuthorizationError when
        session_id is unknown; an unknown session must never resolve to a
        caller-supplied account.
        """
        ...

    def get(self, session_id: UUID) -> SessionState:
        ...

    def save(self, session: SessionState, expected_version: int) -> SessionState:
        """Persist session, enforcing an optimistic version check.

        Raises netra_api.platform.errors.SessionVersionConflictError when
        expected_version does not match the currently stored version.
        """
        ...
