from datetime import datetime, timezone
from hashlib import sha256
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from netra_api.config import Settings
from netra_api.content.sources.ingestion import (
    SOURCE_VERSION_INGESTION_REQUESTED,
    SourceIngestionService,
)
from netra_api.db.models import JobRow, OutboxRow, SourceRow, SourceVersionRow
from netra_api.platform.auth_context import AuthContext
from netra_api.platform.errors import AuthorizationError
from netra_api.platform.database import create_engine, create_session_factory
from netra_worker.runtime.outbox_consumer import OutboxConsumer


pytestmark = pytest.mark.integration


@pytest.fixture
async def db_session() -> AsyncSession:
    engine = create_engine(Settings())
    factory: async_sessionmaker[AsyncSession] = create_session_factory(engine)
    async with factory() as session:
        yield session
    await engine.dispose()


def _auth(account_id):
    return AuthContext(account_id=account_id, session_id=uuid4(), request_id=uuid4(),
                       issued_at=datetime.now(timezone.utc))


async def _cleanup(session: AsyncSession, source_id, version_id, job_id, event_id):
    await session.execute(delete(OutboxRow).where(OutboxRow.event_id == event_id))
    await session.execute(delete(JobRow).where(JobRow.job_id == job_id))
    await session.execute(delete(SourceVersionRow).where(SourceVersionRow.source_version_id == version_id))
    await session.execute(delete(SourceRow).where(SourceRow.source_id == source_id))
    await session.commit()


@pytest.mark.asyncio
async def test_source_version_parse_job_and_outbox_are_created_atomically_and_replay_safe(db_session):
    auth = _auth(uuid4())
    service = SourceIngestionService(db_session)
    key = f"create-source:{uuid4()}"
    content_hash = sha256(b"source").hexdigest()
    result = await service.create_source_and_schedule_parse(
        auth, "Atomic source", "sources/atomic.pdf", "application/pdf", content_hash,
        operation_key=key,
    )
    replay = await service.create_source_and_schedule_parse(
        auth, "Atomic source", "sources/atomic.pdf", "application/pdf", content_hash,
        operation_key=key,
    )
    assert replay == result
    assert (await db_session.execute(select(JobRow).where(JobRow.job_id == result.parse_job_id))).scalar_one().payload["object_key"] == "sources/atomic.pdf"
    event = (await db_session.execute(select(OutboxRow).where(OutboxRow.event_id == result.outbox_event_id))).scalar_one()
    assert event.event_type == SOURCE_VERSION_INGESTION_REQUESTED
    assert event.payload["operation_key"] == f"parse_document:{result.source_version_id}"
    assert (await db_session.execute(select(JobRow).where(JobRow.operation_key == event.payload["operation_key"]))).scalars().all().__len__() == 1
    await _cleanup(db_session, result.source_id, result.source_version_id,
                   result.parse_job_id, result.outbox_event_id)


@pytest.mark.asyncio
async def test_replayed_operation_cannot_cross_account_scope(db_session):
    first_auth = _auth(uuid4())
    service = SourceIngestionService(db_session)
    key = f"cross-account:{uuid4()}"
    result = await service.create_source_and_schedule_parse(
        first_auth, "Scoped source", "sources/scoped.pdf", "application/pdf",
        sha256(b"scoped").hexdigest(), operation_key=key,
    )
    with pytest.raises(AuthorizationError):
        await service.create_source_and_schedule_parse(
            _auth(uuid4()), "Scoped source", "sources/scoped.pdf", "application/pdf",
            sha256(b"scoped").hexdigest(), operation_key=key,
        )
    await _cleanup(db_session, result.source_id, result.source_version_id,
                   result.parse_job_id, result.outbox_event_id)


@pytest.mark.asyncio
async def test_creation_transaction_failure_leaves_no_partial_records(db_session):
    auth = _auth(uuid4())
    key = f"atomic-failure:{uuid4()}"
    source_id = uuid5(NAMESPACE_URL, f"netra:source:{key}")
    version_id = uuid5(source_id, "version:1")
    db_session.add(SourceRow(source_id=source_id, account_id=auth.account_id,
                             title="pre-existing", created_at=datetime.now(timezone.utc)))
    await db_session.commit()
    with pytest.raises(Exception):
        await SourceIngestionService(db_session).create_source_and_schedule_parse(
            auth, "Failed source", "sources/failure.pdf", "application/pdf",
            sha256(b"failure").hexdigest(), operation_key=key,
        )
    await db_session.rollback()
    assert (await db_session.execute(select(SourceRow).where(SourceRow.source_id == source_id))).scalar_one_or_none() is not None
    assert (await db_session.execute(select(SourceVersionRow).where(SourceVersionRow.source_version_id == version_id))).scalar_one_or_none() is None
    assert (await db_session.execute(select(JobRow).where(JobRow.operation_key == f"parse_document:{version_id}"))).scalar_one_or_none() is None
    await db_session.execute(delete(SourceRow).where(SourceRow.source_id == source_id))
    await db_session.commit()


@pytest.mark.asyncio
async def test_outbox_consumer_dispatches_known_event_and_acknowledges_after_job_exists(db_session):
    auth = _auth(uuid4())
    service = SourceIngestionService(db_session)
    result = await service.create_source_and_schedule_parse(
        auth, "Dispatch source", "sources/dispatch.pdf", "application/pdf",
        sha256(b"dispatch").hexdigest(), operation_key=f"dispatch:{uuid4()}",
    )
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    consumer = OutboxConsumer(factory, "test-outbox", poll_interval_seconds=0.01)
    assert await consumer.poll_once() >= 1
    async with factory() as verify:
        event = (await verify.execute(select(OutboxRow).where(OutboxRow.event_id == result.outbox_event_id))).scalar_one()
        job = (await verify.execute(select(JobRow).where(JobRow.job_id == result.parse_job_id))).scalar_one()
        assert event.processed_at is not None
        assert job.job_type == "parse_document"
    await _cleanup(db_session, result.source_id, result.source_version_id,
                   result.parse_job_id, result.outbox_event_id)


@pytest.mark.asyncio
async def test_unknown_outbox_event_remains_pending(db_session):
    event = OutboxRow(event_id=uuid4(), aggregate_type="test", aggregate_id=uuid4(),
                      event_type="unknown.event", payload={}, created_at=datetime.now(timezone.utc),
                      attempt_count=0)
    db_session.add(event)
    await db_session.commit()
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    consumer = OutboxConsumer(factory, "test-outbox", poll_interval_seconds=0.01)
    assert await consumer.poll_once() == 1
    await db_session.refresh(event)
    assert event.processed_at is None
    await db_session.execute(delete(OutboxRow).where(OutboxRow.event_id == event.event_id))
    await db_session.commit()
