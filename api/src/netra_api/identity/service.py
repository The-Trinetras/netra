"""Identity service: resolves authenticated account context for other services.

This is the only place an AuthContext is created, and the only place
account existence/activity and session ownership are checked before that
context is trusted elsewhere in the application.

CLAUDE.md "Data authority and security": "An account ID, source ID or
session ID supplied by a model/client is not authority" and "Services
enforce authorization using authenticated application-supplied context."
Accordingly resolve_auth_context takes an AuthenticatedPrincipal — proof
that a credential was verified — never a bare account_id, and it proves
the session/account binding against PostgreSQL rather than trusting the
session_id it was handed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol
from uuid import UUID

from netra_api.identity.repository import IdentityRepository
from netra_api.platform.auth_context import AuthContext, AuthenticatedPrincipal
from netra_api.platform.errors import AuthorizationError
from netra_api.session.repository import SessionRepository


class CredentialVerifier(Protocol):
    """Verifies a transport-supplied credential into an AuthenticatedPrincipal.

    This is the trust boundary: everything upstream of it is client
    input, everything downstream of it is authenticated. A concrete
    implementation belongs with the transport authentication handler
    (netra_api.transport.http.auth), not here.
    """

    def verify(self, credential: str) -> AuthenticatedPrincipal:
        """Return a principal, or raise
        netra_api.platform.errors.AuthorizationError if the credential is
        absent, malformed, expired or unknown. Never return a principal
        for an unverified credential."""
        ...


class UnconfiguredCredentialVerifier:
    """Fail-closed placeholder used until a real verifier is wired up.

    CLAUDE.md "During editing": "Unimplemented authorization or
    persistence must fail closed, never return success." Authenticating
    is a real product/transport decision that has not been made, so this
    refuses every credential rather than minting a principal.
    """

    def verify(self, credential: str) -> AuthenticatedPrincipal:
        raise AuthorizationError(
            "no credential verifier is configured; authentication is not implemented"
        )


class IdentityService:
    """Builds the authenticated context every other service depends on."""

    def __init__(
        self, repository: IdentityRepository, session_repository: SessionRepository
    ) -> None:
        self._repository = repository
        self._session_repository = session_repository

    def resolve_auth_context(
        self,
        principal: AuthenticatedPrincipal,
        session_id: UUID,
        request_id: UUID,
    ) -> AuthContext:
        """Issue an AuthContext for an already-authenticated principal.

        Three checks must all pass before a context exists:

        1. principal is an AuthenticatedPrincipal, so a credential was
           verified upstream. A caller cannot substitute a bare
           account_id.
        2. The account record exists and is active. A deactivated account
           gets no context.
        3. PostgreSQL agrees that session_id belongs to the principal's
           account. This is what makes
           AuthContext.assert_owns_session meaningful downstream.

        Raises netra_api.platform.errors.AuthorizationError if any check
        fails.
        """

        account = self._repository.get_account(principal.account_id)

        if not account.is_active:
            raise AuthorizationError(f"account {principal.account_id} is not active")

        owner_account_id = self._session_repository.get_owner_account_id(session_id)
        if owner_account_id != principal.account_id:
            raise AuthorizationError(
                f"session {session_id} is not owned by account {principal.account_id}"
            )

        return AuthContext(
            account_id=principal.account_id,
            session_id=session_id,
            request_id=request_id,
            issued_at=datetime.now(timezone.utc),
        )
