"""Authenticated account context passed from transport into application services.

Neither the Coordinator nor the Tutor opens a database connection or
receives raw credentials; every account-scoped call receives this
context from the application layer so services can enforce authorization
before touching PostgreSQL (see CLAUDE.md "Database boundaries").
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel

from netra_api.platform.errors import AuthorizationError


class AuthenticatedPrincipal(BaseModel):
    """Proof that a credential was actually verified for an account.

    CLAUDE.md "Data authority and security": "An account ID, source ID
    or session ID supplied by a model/client is not authority." A bare
    account_id parameter cannot express that rule, because a caller can
    always pass one. This type can only be produced by the credential
    verification boundary (netra_api.identity.service.CredentialVerifier),
    so a function that requires one cannot be handed an unauthenticated
    identifier by mistake.

    Never construct this from a client-supplied value.
    """

    account_id: UUID
    authenticated_at: datetime
    device_id: Optional[UUID] = None
    """Device the verified credential is bound to, when it is device-bound.
    Identity re-checks that device's access on every resolved context, so a
    revoked device loses access during an open connection."""
    credential_expires_at: Optional[datetime] = None
    """Expiry of the verified credential. Expiry remains relevant after the
    connection is accepted (message-flow.md flow 1)."""


class AuthContext(BaseModel):
    """Identifies the authenticated account, session, and request making a call.

    Only netra_api.identity.service.IdentityService.resolve_auth_context
    may build one. It is valid only once that service has verified an
    AuthenticatedPrincipal, confirmed the account is active, and
    confirmed session_id belongs to account_id. Every check below assumes
    that binding already holds.
    """

    account_id: UUID
    session_id: UUID
    request_id: UUID
    issued_at: datetime
    device_id: Optional[UUID] = None

    def assert_owns_session(self, session_id: UUID) -> None:
        """Reject a call targeting a session other than the authorized one.

        This compares against the session this context was issued for. It
        is meaningful only because resolve_auth_context proved that
        binding against PostgreSQL first; on its own it does not
        establish ownership.
        """

        if self.session_id != session_id:
            raise AuthorizationError(
                f"auth context for session {self.session_id} cannot act on session {session_id}"
            )

    def assert_owns_account(self, account_id: UUID) -> None:
        if self.account_id != account_id:
            raise AuthorizationError(
                f"account {self.account_id} may not access account {account_id}"
            )
