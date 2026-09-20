"""U1: the C5 upload and polled job-status routes.

Fixtures throughout (in-memory sources, ingestion and object storage); the
routes were also exercised against a real PostgreSQL and a real PDF, recorded
in docs/team/integration-status.md.
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from netra_api.content.sources.ingestion import source_id_for
from netra_api.content.sources.models import SourceVersion, SourceVersionStatus
from netra_api.platform.errors import AuthorizationError
from netra_api.transport.http.uploads import (
    MAX_JOBS_LISTED,
    create_upload,
    get_job,
    job_id_for,
    list_jobs,
)

NOW = datetime(2026, 9, 20, 9, 0, tzinfo=timezone.utc)
ACCOUNT = UUID("11111111-1111-1111-1111-111111111111")
SESSION = UUID("22222222-2222-2222-2222-222222222222")
PDF = b"%PDF-1.7\nnot really a document, but it starts like one\n"
LIMIT = 16 * 1024 * 1024


class Auth:
    account_id = ACCOUNT


class Identity:
    async def resolve_auth_context(self, principal, session_id, request_id):
        return Auth()


class Services:
    identity = Identity()


class Source:
    def __init__(self, source_id, title, created_at):
        self.source_id, self.title, self.created_at = source_id, title, created_at
        self.account_id = ACCOUNT


class Sources:
    """Minimal account-scoped source repository."""

    def __init__(self):
        self.sources: dict[UUID, Source] = {}
        self.versions: dict[UUID, list[SourceVersion]] = {}

    async def list_sources(self, auth):
        return list(self.sources.values())

    async def get_source(self, auth, source_id):
        if source_id not in self.sources:
            raise AuthorizationError("source is not accessible")
        return self.sources[source_id]

    async def list_versions(self, auth, source_id):
        return self.versions.get(source_id, [])

    async def get_version(self, auth, source_version_id):
        for versions in self.versions.values():
            for version in versions:
                if version.source_version_id == source_version_id:
                    return version
        raise AuthorizationError("version is not accessible")


class Creation:
    def __init__(self, source_id, source_version_id):
        self.source_id, self.source_version_id = source_id, source_version_id


class Ingestion:
    """Stands in for SourceIngestionService: same operation_key replay rule."""

    def __init__(self, sources: Sources):
        self.sources, self.calls, self.clock = sources, 0, NOW

    async def create_source_and_schedule_parse(self, auth, title, object_key, content_type,
                                               content_hash, *, operation_key, **kwargs):
        self.calls += 1
        source_id = source_id_for(operation_key)
        if source_id in self.sources.sources:
            # Replay: the original version, whatever bytes arrived this time.
            return Creation(source_id, self.sources.versions[source_id][0].source_version_id)
        version_id = uuid4()
        self.sources.sources[source_id] = Source(source_id, title, self.clock)
        self.sources.versions[source_id] = [SourceVersion(
            source_version_id=version_id, source_id=source_id, version_number=1,
            status=SourceVersionStatus.PENDING, is_active=False, created_at=self.clock,
            object_key=object_key, content_hash=content_hash)]
        self.clock += timedelta(seconds=1)
        return Creation(source_id, version_id)


class Storage:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    async def put_object(self, key, data, content_type):
        self.objects[key] = data


@pytest.fixture
def world():
    sources = Sources()
    return Services(), sources, Ingestion(sources), Storage()


async def _upload(world, *, request_id=None, title="Ohm's Law", data=PDF, limit=LIMIT,
                  sources=True, ingestion=True, storage=True):
    services, srcs, ing, store = world
    return await create_upload(
        services, srcs if sources else None, ing if ingestion else None, store if storage else None,
        None, limit, object(), SESSION,
        {"request_id": str(request_id or uuid4()), "title": title}, data)


# --- POST /uploads -----------------------------------------------------------------


async def test_a_first_upload_is_accepted_and_schedules_processing(world):
    status, body = await _upload(world)
    assert status == 202
    job = body["job"]
    assert job["kind"] == "document_ingestion"
    assert job["state"] == "processing"
    assert job["title"] == "Ohm's Law"
    assert job["poll_after_ms"] > 0
    # The bytes reached private storage before the canonical row referenced them.
    assert list(world[3].objects.values()) == [PDF]


async def test_the_job_id_is_not_the_source_id(world):
    """C5: the job id is opaque, not a source or provider id."""

    _, body = await _upload(world)
    job = body["job"]
    assert job["job_id"] != job["source_id"] != job["source_version_id"]
    assert job["job_id"] != job["source_version_id"]


async def test_retransmitting_the_same_upload_replays_it_instead_of_creating_a_second(world):
    request_id = uuid4()
    first_status, first = await _upload(world, request_id=request_id)
    second_status, second = await _upload(world, request_id=request_id)
    assert first_status == 202 and second_status == 200  # C5: 202 created, 200 replayed
    assert first["job"]["job_id"] == second["job"]["job_id"]
    assert len(world[1].sources) == 1


async def test_the_same_request_id_with_different_content_is_a_conflict(world):
    request_id = uuid4()
    await _upload(world, request_id=request_id)
    status, body = await _upload(world, request_id=request_id, data=PDF + b"different")
    assert status == 409
    assert body["error"]["code"] == "REQUEST_ID_CONFLICT"


async def test_different_uploads_do_not_share_an_object_key(world):
    """A conflicting retransmission must not overwrite the original's bytes."""

    request_id = uuid4()
    await _upload(world, request_id=request_id)
    await _upload(world, request_id=request_id, data=PDF + b"different")
    assert len(world[3].objects) == 2
    assert PDF in world[3].objects.values()


@pytest.mark.parametrize("data", [b"", b"just text, no header", b"\x00\x01\x02"])
async def test_an_unreadable_file_is_rejected_before_any_work_is_scheduled(world, data):
    status, body = await _upload(world, data=data)
    assert status == 422
    assert body["error"]["code"] == "INVALID_REQUEST"
    assert body["error"]["details"]["field"] == "file"  # C5
    assert world[2].calls == 0 and world[3].objects == {}


async def test_a_file_over_the_approved_limit_is_rejected(world):
    status, body = await _upload(world, data=PDF + b"x" * 64, limit=len(PDF))
    assert status == 422
    assert body["error"]["details"]["field"] == "file"
    assert world[3].objects == {}


async def test_uploads_fail_closed_until_a_maximum_size_is_approved(world):
    """D-UPLOAD-SIZE: no default is invented."""

    status, body = await _upload(world, limit=None)
    assert status == 503
    assert body["error"]["code"] == "RESOURCE_UNAVAILABLE"
    assert world[2].calls == 0 and world[3].objects == {}


@pytest.mark.parametrize("missing", ["sources", "ingestion", "storage"])
async def test_uploads_fail_closed_when_a_dependency_is_unregistered(world, missing):
    status, body = await _upload(world, **{missing: False})
    assert status == 503
    assert body["error"]["code"] == "RESOURCE_UNAVAILABLE"


@pytest.mark.parametrize("fields", [
    {"request_id": "not-a-uuid", "title": "x"},
    {"request_id": str(uuid4()), "title": ""},
    {"request_id": str(uuid4()), "title": "x" * 201},
    {"request_id": None, "title": "x"},
])
async def test_invalid_upload_fields_are_rejected(world, fields):
    services, srcs, ing, store = world
    status, body = await create_upload(services, srcs, ing, store, None, LIMIT,
                                       object(), SESSION, fields, PDF)
    assert status == 422
    assert body["error"]["code"] == "INVALID_REQUEST"
    assert ing.calls == 0


# --- GET /jobs and /jobs/{job_id} --------------------------------------------------


async def test_a_job_can_be_polled_by_its_id(world):
    services, srcs, _, _ = world
    _, created = await _upload(world)
    status, body = await get_job(services, srcs, None, object(), SESSION,
                                 UUID(created["job"]["job_id"]))
    assert status == 200
    assert body["job"]["job_id"] == created["job"]["job_id"]


async def test_an_unknown_job_is_denied_rather_than_reported_missing(world):
    """C5: the route must never be an existence oracle."""

    services, srcs, _, _ = world
    await _upload(world)
    status, body = await get_job(services, srcs, None, object(), SESSION, uuid4())
    assert status == 403
    assert body["error"]["code"] == "AUTHORIZATION_DENIED"


async def test_another_accounts_job_is_denied_the_same_way(world):
    """Same answer as unknown, so the two cannot be told apart."""

    services, srcs, _, _ = world
    await _upload(world)
    stranger = job_id_for(source_id_for("upload:99999999-9999-9999-9999-999999999999:x"))
    status, body = await get_job(services, srcs, None, object(), SESSION, stranger)
    assert status == 403
    assert body["error"]["code"] == "AUTHORIZATION_DENIED"


async def test_jobs_are_listed_newest_first_and_bounded(world):
    services, srcs, _, _ = world
    for index in range(MAX_JOBS_LISTED + 5):
        await _upload(world, title=f"doc {index}")
    status, body = await list_jobs(services, srcs, None, object(), SESSION)
    assert status == 200
    jobs = body["jobs"]
    assert len(jobs) == MAX_JOBS_LISTED
    created = [job["created_at"] for job in jobs]
    assert created == sorted(created, reverse=True)
    assert jobs[0]["title"] == f"doc {MAX_JOBS_LISTED + 4}"


async def test_listing_jobs_without_source_storage_fails_closed(world):
    services, _, _, _ = world
    status, body = await list_jobs(services, None, None, object(), SESSION)
    assert status == 503
    assert body["error"]["code"] == "RESOURCE_UNAVAILABLE"


async def test_a_ready_version_is_reported_ready_and_stops_polling(world):
    """C5: ready means the source can be opened; polling stops."""

    services, srcs, _, _ = world
    _, created = await _upload(world)
    source_id = UUID(created["job"]["source_id"])
    version = srcs.versions[source_id][0]
    srcs.versions[source_id][0] = version.model_copy(
        update={"status": SourceVersionStatus.READY, "is_active": True})
    status, body = await get_job(services, srcs, None, object(), SESSION,
                                 UUID(created["job"]["job_id"]))
    assert status == 200
    assert body["job"]["state"] == "ready"
    assert "poll_after_ms" not in body["job"] and "stage" not in body["job"]


async def test_a_failed_version_is_reported_failed_with_a_safe_reason(world):
    services, srcs, _, _ = world
    _, created = await _upload(world)
    source_id = UUID(created["job"]["source_id"])
    version = srcs.versions[source_id][0]
    srcs.versions[source_id][0] = version.model_copy(update={"status": SourceVersionStatus.FAILED})
    status, body = await get_job(services, srcs, None, object(), SESSION,
                                 UUID(created["job"]["job_id"]))
    assert status == 200
    job = body["job"]
    assert job["state"] == "failed"
    assert job["failure"]["reason"] == "processing_failed"
    assert "poll_after_ms" not in job
