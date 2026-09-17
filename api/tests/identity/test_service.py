"""Authorization must fail closed at the point an AuthContext is minted.

CLAUDE.md: "An account ID, source ID or session ID supplied by a
model/client is not authority" and "Unimplemented authorization or
persistence must fail closed, never return success."
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from netra_api.identity.memory import InMemoryIdentityRepository
from netra_api.identity.models import Account, DeviceAccess, SessionBinding, StoredCredential
from netra_api.identity.service import (
    IdentityService,
    StoredCredentialVerifier,
    UnconfiguredCredentialVerifier,
    credential_digest,
)
from netra_api.platform.auth_context import AuthenticatedPrincipal
from netra_api.platform.errors import AuthenticationRequiredError, AuthorizationError, IdempotencyConflictError

NOW = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)


def _clock():
    return NOW


def _principal(account_id, device_id=None, expires_at=None):
    return AuthenticatedPrincipal(
        account_id=account_id, authenticated_at=NOW, device_id=device_id, credential_expires_at=expires_at
    )


async def _repo(account_id, *, active=True, session_owner=None, session_id=None, revoked_binding=False):
    repository = InMemoryIdentityRepository()
    repository.add_account(Account(account_id=account_id, is_active=active))
    if session_id is not None:
        await repository.create_session_binding(
            SessionBinding(
                session_id=session_id,
                account_id=session_owner or account_id,
                created_at=NOW,
                revoked_at=NOW if revoked_binding else None,
            )
        )
    return repository


async def test_unconfigured_verifier_refuses_every_credential():
    """Authentication is unimplemented, so it must deny rather than mint."""

    with pytest.raises(AuthenticationRequiredError):
        await UnconfiguredCredentialVerifier().verify("any-token")


async def test_active_account_owning_the_session_gets_a_context():
    account_id, session_id = uuid4(), uuid4()
    service = IdentityService(await _repo(account_id, session_id=session_id), clock=_clock)

    auth = await service.resolve_auth_context(_principal(account_id), session_id, uuid4())

    assert auth.account_id == account_id
    assert auth.session_id == session_id


async def test_inactive_account_gets_no_context():
    account_id, session_id = uuid4(), uuid4()
    service = IdentityService(await _repo(account_id, active=False, session_id=session_id), clock=_clock)

    with pytest.raises(AuthorizationError):
        await service.resolve_auth_context(_principal(account_id), session_id, uuid4())


async def test_session_belonging_to_another_account_is_refused():
    """The cross-account leak the audit found: a caller naming someone
    else's session previously received a context authorizing it."""

    caller, session_id = uuid4(), uuid4()
    service = IdentityService(await _repo(caller, session_id=session_id, session_owner=uuid4()), clock=_clock)

    with pytest.raises(AuthorizationError):
        await service.resolve_auth_context(_principal(caller), session_id, uuid4())


async def test_unknown_session_is_refused_like_a_foreign_one():
    account_id = uuid4()
    service = IdentityService(await _repo(account_id), clock=_clock)
    with pytest.raises(AuthorizationError) as raised:
        await service.resolve_auth_context(_principal(account_id), uuid4(), uuid4())
    assert str(raised.value) == "session access denied"


async def test_revoked_binding_is_refused():
    account_id, session_id = uuid4(), uuid4()
    service = IdentityService(await _repo(account_id, session_id=session_id, revoked_binding=True), clock=_clock)
    with pytest.raises(AuthorizationError):
        await service.resolve_auth_context(_principal(account_id), session_id, uuid4())


async def test_revoked_device_loses_access_on_the_next_message():
    account_id, session_id, device_id = uuid4(), uuid4(), uuid4()
    repository = await _repo(account_id, session_id=session_id)
    repository.add_device(DeviceAccess(device_id=device_id, account_id=account_id))
    service = IdentityService(repository, clock=_clock)
    principal = _principal(account_id, device_id=device_id)
    assert (await service.resolve_auth_context(principal, session_id, uuid4())).device_id == device_id

    repository.add_device(DeviceAccess(device_id=device_id, account_id=account_id, revoked_at=NOW))
    with pytest.raises(AuthorizationError):
        await service.resolve_auth_context(principal, session_id, uuid4())


async def test_credential_expiring_after_accept_requires_sign_in_again():
    account_id, session_id = uuid4(), uuid4()
    service = IdentityService(await _repo(account_id, session_id=session_id), clock=_clock)
    with pytest.raises(AuthenticationRequiredError):
        await service.resolve_auth_context(_principal(account_id, expires_at=NOW), session_id, uuid4())


async def test_stored_credential_verifier_accepts_only_live_issued_tokens():
    account_id, device_id = uuid4(), uuid4()
    repository = await _repo(account_id)

    def store(token, **overrides):
        values = dict(credential_id=uuid4(), token_sha256=credential_digest(token), account_id=account_id, device_id=device_id, issued_at=NOW - timedelta(hours=1), expires_at=NOW + timedelta(hours=1))
        values.update(overrides)
        repository.add_credential(StoredCredential(**values))

    store("good-token")
    store("expired-token", expires_at=NOW)
    store("revoked-token", revoked_at=NOW)
    verifier = StoredCredentialVerifier(repository, clock=_clock)

    principal = await verifier.verify("good-token")
    assert (principal.account_id, principal.device_id) == (account_id, device_id)
    for token in (None, "", "unknown-token", "expired-token", "revoked-token", "x" * 5000):
        with pytest.raises(AuthenticationRequiredError):
            await verifier.verify(token)
    assert "good-token" not in repository.credentials  # only digests are stored


async def test_new_session_binds_to_the_verified_principal_only_once():
    account_id, session_id = uuid4(), uuid4()
    repository = await _repo(account_id)
    service = IdentityService(repository, clock=_clock)
    binding = await service.bind_new_session(_principal(account_id), session_id)
    assert binding.account_id == account_id
    intruder = uuid4()
    repository.add_account(Account(account_id=intruder))
    with pytest.raises(IdempotencyConflictError):
        await service.bind_new_session(_principal(intruder), session_id)
    assert (await repository.get_session_binding(session_id)).account_id == account_id
