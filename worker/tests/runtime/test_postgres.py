import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from netra_api.content.settings import ContentSettings
from netra_api.db.models import JobRow, OutboxRow
from netra_api.platform.database import create_engine, create_session_factory
from netra_worker.runtime.job_repository import JobPayload
from netra_worker.runtime.postgres import AsyncJobRepository, AsyncOutboxRepository


pytestmark = pytest.mark.integration


class _Payload(JobPayload):
    value: str = "test"


@pytest.fixture
async def db_session() -> AsyncSession:
    url = os.environ.get("NETRA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("NETRA_TEST_DATABASE_URL (a disposable local database) is not set")
    engine = create_engine(url)
    factory: async_sessionmaker[AsyncSession] = create_session_factory(engine)
    async with factory() as session:
        try:
            yield session
        finally:
            await session.rollback()
    await engine.dispose()


@pytest.mark.asyncio
async def test_job_operation_key_is_idempotent(db_session: AsyncSession):
    repo = AsyncJobRepository(db_session)
    key = f"test-operation-{uuid4()}"
    first = await repo.enqueue("test", _Payload(idempotency_key=key), key)
    second = await repo.enqueue("test", _Payload(idempotency_key=key, value="different"), key)
    assert second.job_id == first.job_id
    await db_session.execute(delete(JobRow).where(JobRow.job_id == first.job_id))
    await db_session.commit()


@pytest.mark.asyncio
async def test_job_lease_owner_and_expiry_are_enforced(db_session: AsyncSession):
    repo = AsyncJobRepository(db_session)
    key = f"test-lease-{uuid4()}"
    created = await repo.enqueue("test", _Payload(idempotency_key=key), key)
    lease_one = await repo.claim_next(["test"], "worker-one", 60)
    assert lease_one is not None
    with pytest.raises(RuntimeError):
        await repo.complete(created.job_id, lease_one.lease.model_copy(update={"worker_id": "worker-two"}))
    await db_session.execute(update(JobRow).where(JobRow.job_id == created.job_id).values(
        lease_until=datetime.now(timezone.utc) - timedelta(seconds=1)))
    await db_session.commit()
    lease_two = await repo.claim_next(["test"], "worker-two", 60)
    assert lease_two is not None
    assert lease_two.lease.worker_id == "worker-two"
    await repo.complete(created.job_id, lease_two.lease)
    await db_session.execute(delete(JobRow).where(JobRow.job_id == created.job_id))
    await db_session.commit()


@pytest.mark.asyncio
async def test_outbox_claim_and_processed_state_are_idempotency_boundaries(db_session: AsyncSession):
    repo = AsyncOutboxRepository(db_session)
    event = await repo.enqueue("test", uuid4(), "test.created", {"operation": str(uuid4())})
    await db_session.commit()
    claimed = await repo.claim_unprocessed(10, "outbox-worker", 60)
    assert [item.event_id for item in claimed] == [event.event_id]
    await repo.mark_processed(event.event_id, datetime.now(timezone.utc),
                              claimed[0].claim_token, claimed[0].claim_worker_id)
    assert await repo.claim_unprocessed(10, "outbox-worker", 60) == []
    await db_session.execute(delete(OutboxRow).where(OutboxRow.event_id == event.event_id))
    await db_session.commit()


@pytest.mark.asyncio
async def test_outbox_claims_are_exclusive_and_expired_claims_reclaim(db_session: AsyncSession):
    repo = AsyncOutboxRepository(db_session)
    event = await repo.enqueue("test", uuid4(), "test.created", {"operation": str(uuid4())})
    await db_session.commit()
    first = await repo.claim_unprocessed(1, "worker-one", 60)
    assert first and first[0].claim_token

    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    async with factory() as second_session:
        second = AsyncOutboxRepository(second_session)
        assert await second.claim_unprocessed(1, "worker-two", 60) == []
        with pytest.raises(RuntimeError):
            await second.mark_processed(event.event_id, datetime.now(timezone.utc),
                                        uuid4(), "worker-two")

    await db_session.execute(update(OutboxRow).where(OutboxRow.event_id == event.event_id).values(
        claim_until=datetime.now(timezone.utc) - timedelta(seconds=1)))
    await db_session.commit()
    reclaimed = await repo.claim_unprocessed(1, "worker-two", 60)
    assert reclaimed[0].claim_worker_id == "worker-two"
    await repo.mark_processed(event.event_id, datetime.now(timezone.utc),
                              reclaimed[0].claim_token, reclaimed[0].claim_worker_id)
    await db_session.execute(delete(OutboxRow).where(OutboxRow.event_id == event.event_id))
    await db_session.commit()
