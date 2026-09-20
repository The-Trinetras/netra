"""D-CRED sign-in: operator-issued one-time codes exchanged for device credentials.

In-memory repository (labelled fixture) for policy; the PostgreSQL path is
test_access_code_postgres.py (integration).
"""

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from netra_api.bootstrap import UnavailableRepository
from netra_api.identity.access_codes import REPLAY_WINDOW, CredentialIssued, ExchangeRequest
from netra_api.identity.memory import InMemoryIdentityRepository
from netra_api.identity.models import Account
from netra_api.identity.provisioning import issue_access_code
from netra_api.identity.service import IdentityService, StoredCredentialVerifier
from netra_api.platform.errors import AuthenticationRequiredError, InvalidRequestError
from netra_api.transport.http.device_credentials import exchange_device_credential

START = datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def world():
    repo, clock = InMemoryIdentityRepository(), Clock()
    return repo, clock, IdentityService(repo, clock=clock), StoredCredentialVerifier(repo, clock=clock)


async def _issue(repo, **overrides):
    options = dict(code_valid_for=timedelta(days=7), credential_lifetime=timedelta(days=120), now=START)
    options.update(overrides)
    return await issue_access_code(repo, **options)


def _request(code, request_id=None):
    return ExchangeRequest(access_code=code, request_id=request_id or uuid4())


async def test_exchange_issues_a_credential_the_verifier_accepts(world):
    repo, clock, identity, verifier = world
    issued = await _issue(repo)
    credential = await identity.exchange_access_code(_request(issued.code))

    principal = await verifier.verify(credential.credential)
    assert principal.account_id == issued.account_id and principal.device_id is not None
    assert credential.expires_at == START + timedelta(days=120)
    assert (await repo.get_device(issued.account_id, principal.device_id)).is_active


async def test_code_is_accepted_as_a_student_might_type_it(world):
    repo, _, identity, _ = world
    issued = await _issue(repo)
    typed = issued.code.lower().replace("-", " ").replace("0", "o")
    assert await identity.exchange_access_code(_request(typed))


async def test_a_used_code_cannot_be_exchanged_again_and_the_first_credential_stays(world):
    repo, _, identity, verifier = world
    issued = await _issue(repo)
    first = await identity.exchange_access_code(_request(issued.code))
    with pytest.raises(AuthenticationRequiredError):
        await identity.exchange_access_code(_request(issued.code))
    assert await verifier.verify(first.credential)


async def test_same_request_id_within_the_window_replaces_the_credential_on_the_same_device(world):
    repo, clock, identity, verifier = world
    issued = await _issue(repo)
    request_id = uuid4()
    first = await identity.exchange_access_code(_request(issued.code, request_id))
    device = (await verifier.verify(first.credential)).device_id

    clock.now = START + REPLAY_WINDOW - timedelta(seconds=1)
    second = await identity.exchange_access_code(_request(issued.code, request_id))

    assert second.credential != first.credential
    assert (await verifier.verify(second.credential)).device_id == device
    with pytest.raises(AuthenticationRequiredError):
        await verifier.verify(first.credential)
    assert len(repo.devices) == 1


async def test_replay_window_is_measured_from_the_first_exchange(world):
    repo, clock, identity, _ = world
    issued = await _issue(repo)
    request_id = uuid4()
    await identity.exchange_access_code(_request(issued.code, request_id))
    clock.now = START + timedelta(minutes=10)
    await identity.exchange_access_code(_request(issued.code, request_id))
    clock.now = START + REPLAY_WINDOW
    with pytest.raises(AuthenticationRequiredError):
        await identity.exchange_access_code(_request(issued.code, request_id))


async def test_expired_unknown_and_revoked_codes_are_refused_alike(world):
    repo, clock, identity, _ = world
    expired = await _issue(repo, code_valid_for=timedelta(hours=1))
    revoked = await _issue(repo)
    record = next(r for r in repo.access_codes.values() if r.account_id == revoked.account_id)
    repo.access_codes[record.code_sha256] = record.model_copy(update={"revoked_at": START})

    clock.now = START + timedelta(hours=1)
    for code in (expired.code, revoked.code, "7K3Q-M9TX-2BWD"):
        with pytest.raises(AuthenticationRequiredError) as caught:
            await identity.exchange_access_code(_request(code))
        assert str(caught.value) == "access code not accepted"
    assert repo.devices == {} and repo.credentials == {}


async def test_inactive_account_cannot_exchange(world):
    repo, _, identity, _ = world
    issued = await _issue(repo)
    repo.add_account(Account(account_id=issued.account_id, is_active=False))
    with pytest.raises(AuthenticationRequiredError):
        await identity.exchange_access_code(_request(issued.code))


async def test_malformed_code_is_an_invalid_request(world):
    _, _, identity, _ = world
    with pytest.raises(InvalidRequestError) as caught:
        await identity.exchange_access_code(_request("not a code"))
    assert caught.value.field == "access_code"


async def test_concurrent_exchanges_with_different_request_ids_give_one_credential(world):
    repo, _, identity, _ = world
    issued = await _issue(repo)
    results = await asyncio.gather(*(identity.exchange_access_code(_request(issued.code)) for _ in range(5)), return_exceptions=True)
    assert sum(isinstance(r, CredentialIssued) for r in results) == 1
    assert sum(isinstance(r, AuthenticationRequiredError) for r in results) == 4


async def test_issuance_requires_bounded_lifetimes(world):
    repo = world[0]
    for options in ({"code_valid_for": timedelta(days=31)}, {"code_valid_for": timedelta(0)},
                    {"credential_lifetime": timedelta(days=181)}, {"credential_lifetime": timedelta(0)}):
        with pytest.raises(ValueError):
            await _issue(repo, **options)
    assert repo.access_codes == {}


async def test_issuing_for_an_existing_account_keeps_it():
    repo = InMemoryIdentityRepository()
    account = uuid4()
    issued = await _issue(repo, account_id=account)
    assert issued.account_id == account and account in repo.accounts


# ------------------------------------------------------------------ HTTP handler


async def test_http_201_body_matches_the_contract(world):
    repo, _, identity, _ = world
    issued = await _issue(repo)
    status, body = await exchange_device_credential(identity, {"access_code": issued.code, "request_id": str(uuid4())})
    assert status == 201 and set(body) == {"credential", "expires_at"}
    CredentialIssued.model_validate(body)


@pytest.mark.parametrize(
    "body, status, code",
    [
        ({"access_code": "7K3Q-M9TX-2BWD", "request_id": "b4c1f0de-0000-4000-8000-000000000001"}, 401, "AUTH_REQUIRED"),
        ({"access_code": "7K3Q-M9TX-2BWD"}, 422, "INVALID_REQUEST"),
        ({"access_code": "nope", "request_id": "b4c1f0de-0000-4000-8000-000000000001"}, 422, "INVALID_REQUEST"),
        (None, 422, "INVALID_REQUEST"),
    ],
)
async def test_http_errors_use_the_typed_payload(world, body, status, code):
    identity = world[2]
    got_status, got = await exchange_device_credential(identity, body)
    assert got_status == status and got["error"]["code"] == code
    assert "credential" not in got


async def test_http_503_when_no_database_is_configured():
    identity = IdentityService(UnavailableRepository())
    status, body = await exchange_device_credential(identity, {"access_code": "7K3Q-M9TX-2BWD", "request_id": str(uuid4())})
    assert status == 503 and body["error"]["code"] == "RESOURCE_UNAVAILABLE" and body["error"]["retryable"] is True


async def test_route_is_unauthenticated_and_never_cached(world):
    pytest.importorskip("fastapi")
    import httpx
    from types import SimpleNamespace

    from netra_api.main import create_app

    repo, _, identity, _ = world
    issued = await _issue(repo)
    composition = SimpleNamespace(services=SimpleNamespace(identity=identity), verifier=None, sources=None,
                                  registered={}, telemetry_diagnostics=dict, shutdown=lambda: None)
    transport = httpx.ASGITransport(app=create_app(composition=composition))
    async with httpx.AsyncClient(transport=transport, base_url="http://netra.test") as client:
        ok = await client.post("/v1/device-credentials", json={"access_code": issued.code, "request_id": str(uuid4())})
        bad = await client.post("/v1/device-credentials", content=b"not json", headers={"content-type": "application/json"})
    assert ok.status_code == 201 and ok.headers["cache-control"] == "no-store"
    assert bad.status_code == 422 and bad.headers["cache-control"] == "no-store"
