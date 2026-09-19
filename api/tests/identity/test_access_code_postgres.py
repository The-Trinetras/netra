"""D-CRED exchange on real PostgreSQL: row lock, replay revocation, verifier.

Requires the disposable test database migrated to head (NETRA_TEST_DATABASE_URL).
"""

import asyncio
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from netra_api.identity.access_codes import CredentialIssued, ExchangeRequest
from netra_api.identity.postgres import PostgresIdentityRepository
from netra_api.identity.provisioning import issue_access_code
from netra_api.identity.service import IdentityService, StoredCredentialVerifier
from netra_api.platform.database import create_engine
from netra_api.platform.errors import AuthenticationRequiredError

pytestmark = pytest.mark.integration


@pytest.fixture
async def repo():
    url = os.environ.get("NETRA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("NETRA_TEST_DATABASE_URL (a disposable local database) is not set")
    engine = create_engine(url)
    try:
        yield PostgresIdentityRepository(engine)
    finally:
        await engine.dispose()


async def _issue(repo):
    return await issue_access_code(repo, code_valid_for=timedelta(days=1), credential_lifetime=timedelta(days=30))


async def test_exchange_and_replay_on_postgres(repo):
    issued = await _issue(repo)
    identity, verifier = IdentityService(repo), StoredCredentialVerifier(repo)
    request_id = uuid4()

    first = await identity.exchange_access_code(ExchangeRequest(access_code=issued.code, request_id=request_id))
    principal = await verifier.verify(first.credential)
    assert principal.account_id == issued.account_id

    second = await identity.exchange_access_code(ExchangeRequest(access_code=issued.code, request_id=request_id))
    assert (await verifier.verify(second.credential)).device_id == principal.device_id
    with pytest.raises(AuthenticationRequiredError):
        await verifier.verify(first.credential)
    with pytest.raises(AuthenticationRequiredError):
        await identity.exchange_access_code(ExchangeRequest(access_code=issued.code, request_id=uuid4()))


async def test_concurrent_exchanges_yield_exactly_one_credential(repo):
    issued = await _issue(repo)
    identity = IdentityService(repo)
    results = await asyncio.gather(
        *(identity.exchange_access_code(ExchangeRequest(access_code=issued.code, request_id=uuid4())) for _ in range(5)),
        return_exceptions=True,
    )
    assert sum(isinstance(r, CredentialIssued) for r in results) == 1
    assert sum(isinstance(r, AuthenticationRequiredError) for r in results) == 4


async def test_expired_code_is_refused_on_postgres(repo):
    issued = await _issue(repo)
    later = datetime.now(timezone.utc) + timedelta(days=2)
    identity = IdentityService(repo, clock=lambda: later)
    with pytest.raises(AuthenticationRequiredError):
        await identity.exchange_access_code(ExchangeRequest(access_code=issued.code, request_id=uuid4()))
