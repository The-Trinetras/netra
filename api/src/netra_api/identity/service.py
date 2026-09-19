"""Identity service: verifies credentials and resolves authenticated context.

This is the only place an AuthContext is created, and the only place
account activity, device access and the account–session binding are
checked before that context is trusted elsewhere.

CLAUDE.md: "An account ID, source ID or session ID supplied by a
model/client is not authority." resolve_auth_context therefore takes an
AuthenticatedPrincipal — proof a credential was verified — never a bare
account_id, and proves the session binding against the Identity repository
rather than trusting the session_id it was handed.

Checks run for every inbound message, not once per connection, because
expiry, device revocation and binding revocation remain relevant after a
WebSocket is accepted (message-flow.md flow 1).
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from typing import Callable, Optional, Protocol
from uuid import UUID

from netra_api.identity.access_codes import (
    CredentialIssued,
    ExchangeRequest,
    access_code_digest,
    new_credential_token,
    normalize_access_code,
)
from netra_api.identity.models import SessionBinding
from netra_api.identity.repository import IdentityRepository
from netra_api.platform.auth_context import AuthContext, AuthenticatedPrincipal
from netra_api.platform.errors import AuthenticationRequiredError, AuthorizationError, InvalidRequestError

Clock = Callable[[], datetime]

MAX_CREDENTIAL_CHARS = 4096
"""Upper bound on a presented credential before hashing. A defensive input
bound, not a token-format policy."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def credential_digest(credential: str) -> str:
    """SHA-256 hex digest under which an issued credential is stored.

    Appropriate only for high-entropy issued tokens, which is the only kind
    this verifier accepts; it is not a password hash.
    """

    return hashlib.sha256(credential.encode("utf-8")).hexdigest()


class CredentialVerifier(Protocol):
    """Verifies a transport-supplied credential into an AuthenticatedPrincipal.

    This is the trust boundary: everything upstream is client input,
    everything downstream is authenticated.
    """

    async def verify(self, credential: Optional[str]) -> AuthenticatedPrincipal:
        """Return a principal, or raise AuthenticationRequiredError if the
        credential is absent, malformed, expired, revoked or unknown."""
        ...


class UnconfiguredCredentialVerifier:
    """Fail-closed verifier used whenever no approved verifier is configured.

    CLAUDE.md: "Unimplemented authorization or persistence must fail closed,
    never return success." This refuses every credential rather than minting
    a principal.
    """

    async def verify(self, credential: Optional[str]) -> AuthenticatedPrincipal:
        raise AuthenticationRequiredError(
            "no credential verifier is configured; authentication is not available"
        )


class StoredCredentialVerifier:
    """Verifies bearer credentials previously issued and stored as digests.

    It does not issue credentials; IdentityService.exchange_access_code does
    (decision D-CRED).
    """

    def __init__(self, repository: IdentityRepository, clock: Clock = _utcnow) -> None:
        self._repository = repository
        self._clock = clock

    async def verify(self, credential: Optional[str]) -> AuthenticatedPrincipal:
        if not credential or len(credential) > MAX_CREDENTIAL_CHARS:
            raise AuthenticationRequiredError("credential absent or malformed")

        digest = credential_digest(credential)
        stored = await self._repository.find_credential(digest)
        if stored is None or not hmac.compare_digest(stored.token_sha256, digest):
            raise AuthenticationRequiredError("credential not recognised")

        now = self._clock()
        if stored.revoked_at is not None or stored.expires_at <= now:
            raise AuthenticationRequiredError("credential expired or revoked")

        return AuthenticatedPrincipal(
            account_id=stored.account_id,
            authenticated_at=now,
            device_id=stored.device_id,
            credential_expires_at=stored.expires_at,
        )


class IdentityService:
    """Builds the authenticated context every other service depends on."""

    def __init__(self, repository: IdentityRepository, clock: Clock = _utcnow) -> None:
        self._repository = repository
        self._clock = clock

    async def resolve_auth_context(
        self,
        principal: AuthenticatedPrincipal,
        session_id: UUID,
        request_id: UUID,
    ) -> AuthContext:
        """Issue an AuthContext for an already-authenticated principal.

        All checks must pass before a context exists:

        1. The credential has not expired since it was verified.
        2. The account exists and is active.
        3. A device-bound credential's device still has access.
        4. Identity's binding says session_id belongs to this account and
           the binding is not revoked.

        Unknown and unauthorized collapse into the same AuthorizationError so
        a caller cannot probe for another account's sessions.
        """

        now = self._clock()
        if principal.credential_expires_at is not None and principal.credential_expires_at <= now:
            raise AuthenticationRequiredError("credential expired")

        await self._assert_account_and_device(principal)

        binding = await self._repository.get_session_binding(session_id)
        if binding is None or binding.revoked_at is not None or binding.account_id != principal.account_id:
            raise AuthorizationError("session access denied")

        return AuthContext(
            account_id=principal.account_id,
            session_id=session_id,
            request_id=request_id,
            issued_at=now,
            device_id=principal.device_id,
        )

    async def bind_new_session(self, principal: AuthenticatedPrincipal, session_id: UUID) -> SessionBinding:
        """Bind a freshly created session to the verified principal's account.

        The session_id is server-minted by the caller; a client can never
        choose which account a session binds to.
        """

        await self._assert_account_and_device(principal)
        return await self._repository.create_session_binding(
            SessionBinding(session_id=session_id, account_id=principal.account_id, created_at=self._clock())
        )

    async def exchange_access_code(self, request: ExchangeRequest) -> CredentialIssued:
        """POST /v1/device-credentials (D-CRED): a one-time code for a device credential.

        Every refusal is the same AuthenticationRequiredError, so a caller
        cannot tell an unknown code from an expired, used or revoked one.
        """

        normalized = normalize_access_code(request.access_code)
        if normalized is None:
            raise InvalidRequestError("access code is malformed", field="access_code")
        token = new_credential_token()
        expires_at = await self._repository.exchange_access_code(
            access_code_digest(normalized), request.request_id, credential_digest(token), self._clock()
        )
        if expires_at is None:
            raise AuthenticationRequiredError("access code not accepted")
        return CredentialIssued(credential=token, expires_at=expires_at)

    async def _assert_account_and_device(self, principal: AuthenticatedPrincipal) -> None:
        account = await self._repository.get_account(principal.account_id)
        if account is None or not account.is_active:
            raise AuthorizationError("account access denied")

        if principal.device_id is not None:
            device = await self._repository.get_device(principal.account_id, principal.device_id)
            if device is None or not device.is_active:
                raise AuthorizationError("device access denied")
