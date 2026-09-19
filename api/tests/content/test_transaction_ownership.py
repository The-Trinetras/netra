"""Repositories must never commit or discard another operation's writes.

M2 repositories share one AsyncSession with their caller. Before opening their
own short transaction they close SQLAlchemy's read autobegin. That close must
not publish (commit) or silently discard (roll back) writes that some other
operation left pending on the same session. These tests run against the
disposable PostgreSQL only and observe committed state from a separate engine.
"""

import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from netra_api.content.reading.blocks import BlockType, ReadingBlock, Sentence
from netra_api.content.reading.postgres import AsyncReadingBlockRepository
from netra_api.content.sources.postgres import AsyncSourceRepository
from netra_api.db.models import JobRow, SourceRow
from netra_api.db.transactions import ForeignTransactionError
from netra_api.platform.auth_context import AuthContext
from netra_api.platform.database import create_engine, create_session_factory
from netra_worker.runtime.job_repository import JobPayload
from netra_worker.runtime.postgres import AsyncJobRepository

pytestmark = pytest.mark.integration


class _Payload(JobPayload):
    value: str = "ownership"


@pytest.fixture
async def factory():
    url = os.environ.get("NETRA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("NETRA_TEST_DATABASE_URL (a disposable local database) is not set")
    engine = create_engine(url)
    yield create_session_factory(engine)
    await engine.dispose()


def _auth(account_id):
    return AuthContext(account_id=account_id, session_id=uuid4(), request_id=uuid4(),
                       issued_at=datetime.now(timezone.utc))


async def _committed_source(factory, source_id) -> bool:
    async with factory() as observer:
        return (await observer.execute(select(SourceRow.source_id).where(
            SourceRow.source_id == source_id))).scalar_one_or_none() is not None


async def _foreign_orm_write(session: AsyncSession):
    """Another operation's pending, unflushed ORM insert on the shared session."""
    source_id = uuid4()
    session.add(SourceRow(source_id=source_id, account_id=uuid4(), title="foreign pending",
                          created_at=datetime.now(timezone.utc)))
    return source_id


async def _foreign_core_write(session: AsyncSession):
    """Another operation's executed-but-uncommitted Core insert (not visible in session.new)."""
    source_id = uuid4()
    await session.execute(insert(SourceRow).values(source_id=source_id, account_id=uuid4(),
                                                   title="foreign executed", created_at=datetime.now(timezone.utc)))
    return source_id


async def _ready_version(factory):
    async with factory() as session:
        repo = AsyncSourceRepository(session)
        auth = _auth(uuid4())
        source = await repo.create_source(auth, "owned")
        version = await repo.create_version(auth, source.source_id, object_key="k", parser_name="p",
                                            parser_version="1", content_hash=repo.content_hash_for(b"x"))
    return auth, source, version


async def _cleanup(factory, *source_ids):
    async with factory() as session:
        await session.execute(delete(SourceRow).where(SourceRow.source_id.in_(source_ids)))
        await session.commit()


@pytest.mark.parametrize("foreign_write", [_foreign_orm_write, _foreign_core_write])
async def test_a_repository_write_refuses_to_commit_foreign_pending_writes(factory, foreign_write):
    auth, source, version = await _ready_version(factory)
    async with factory() as shared:
        foreign_id = await foreign_write(shared)
        blocks = AsyncReadingBlockRepository(shared)
        block = ReadingBlock(block_id=uuid4(), source_version_id=version.source_version_id, ordinal=0,
                             block_type=BlockType.PARAGRAPH, sentences=[Sentence(sentence_id=uuid4(), ordinal=0, text="Hi.")])
        with pytest.raises(ForeignTransactionError):
            await blocks.replace_blocks(version.source_version_id, [block])
        # The foreign write was neither published nor discarded by the repository.
        assert not await _committed_source(factory, foreign_id)
        assert shared.in_transaction()
        await shared.rollback()  # its owner decides
    assert not await _committed_source(factory, foreign_id)
    await _cleanup(factory, source.source_id)


async def test_worker_stage_update_and_source_creation_refuse_foreign_writes(factory):
    auth, source, version = await _ready_version(factory)
    async with factory() as shared:
        foreign_id = await _foreign_core_write(shared)
        repo = AsyncSourceRepository(shared)
        with pytest.raises(ForeignTransactionError):
            await repo.mark_stage_complete_internal(version.source_version_id, "parsing")
        with pytest.raises(ForeignTransactionError):
            await repo.create_source(auth, "second")
        assert not await _committed_source(factory, foreign_id)
        await shared.rollback()
    await _cleanup(factory, source.source_id)


async def test_a_read_only_autobegin_is_closed_and_the_write_commits(factory):
    auth, source, version = await _ready_version(factory)
    async with factory() as shared:
        repo = AsyncSourceRepository(shared)
        await repo.get_version(auth, version.source_version_id)  # autobegins a read transaction
        assert shared.in_transaction()
        updated = await repo.mark_stage_complete_internal(version.source_version_id, "parsing")
        assert updated.completed_stages == ["parsing"]
    async with factory() as observer:
        assert (await AsyncSourceRepository(observer).get_version(auth, version.source_version_id)).completed_stages == ["parsing"]
    await _cleanup(factory, source.source_id)


async def test_job_enqueue_neither_publishes_nor_discards_a_callers_writes(factory):
    key = f"ownership-{uuid4()}"
    async with factory() as seed:
        await AsyncJobRepository(seed).enqueue("ownership-a", _Payload(idempotency_key=key), key)
    for job_type in ("ownership-a", "ownership-b"):  # idempotent replay and conflicting type
        async with factory() as shared:
            foreign_id = await _foreign_orm_write(shared)
            with pytest.raises(ForeignTransactionError):
                await AsyncJobRepository(shared).enqueue(job_type, _Payload(idempotency_key=key), key)
            assert foreign_id in {row.source_id for row in shared.new}  # not discarded
            assert not await _committed_source(factory, foreign_id)  # not published
            await shared.rollback()
    async with factory() as session:
        await session.execute(delete(JobRow).where(JobRow.operation_key == key))
        await session.commit()
