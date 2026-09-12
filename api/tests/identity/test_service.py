"""Authorization must fail closed at the point an AuthContext is minted.

CLAUDE.md: "An account ID, source ID or session ID supplied by a
model/client is not authority" and "Unimplemented authorization or
persistence must fail closed, never return success."
"""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from netra_api.identity.models import Account
from netra_api.identity.service import IdentityService, UnconfiguredCredentialVerifier
from netra_api.platform.auth_context import AuthenticatedPrincipal
from netra_api.platform.errors import AuthorizationError


class _FakeIdentityRepository:
    def __init__(self, account):
        self._account = account

    def get_account(self, account_id):
        return self._account


class _FakeSessionRepository:
    def __init__(self, owner_account_id):
        self._owner_account_id = owner_account_id

    def get_owner_account_id(self, session_id):
        return self._owner_account_id

    def get(self, session_id):
        raise NotImplementedError

    def save(self, session, expected_version):
        raise NotImplementedError


def _principal(account_id):
    return AuthenticatedPrincipal(
        account_id=account_id, authenticated_at=datetime.now(timezone.utc)
    )


def test_unconfigured_verifier_refuses_every_credential():
    """Authentication is unimplemented, so it must deny rather than mint."""

    with pytest.raises(AuthorizationError):
        UnconfiguredCredentialVerifier().verify("any-token")


def test_active_account_owning_the_session_gets_a_context():
    account_id = uuid4()
    session_id = uuid4()
    service = IdentityService(
        _FakeIdentityRepository(Account(account_id=account_id, is_active=True)),
        _FakeSessionRepository(owner_account_id=account_id),
    )

    auth = service.resolve_auth_context(_principal(account_id), session_id, uuid4())

    assert auth.account_id == account_id
    assert auth.session_id == session_id


def test_inactive_account_gets_no_context():
    account_id = uuid4()
    service = IdentityService(
        _FakeIdentityRepository(Account(account_id=account_id, is_active=False)),
        _FakeSessionRepository(owner_account_id=account_id),
    )

    with pytest.raises(AuthorizationError):
        service.resolve_auth_context(_principal(account_id), uuid4(), uuid4())


def test_session_belonging_to_another_account_is_refused():
    """The cross-account leak the audit found: a caller naming someone
    else's session previously received a context authorizing it."""

    caller_account_id = uuid4()
    service = IdentityService(
        _FakeIdentityRepository(Account(account_id=caller_account_id, is_active=True)),
        _FakeSessionRepository(owner_account_id=uuid4()),
    )

    with pytest.raises(AuthorizationError):
        service.resolve_auth_context(_principal(caller_account_id), uuid4(), uuid4())
