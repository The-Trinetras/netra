import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from netra_api.content.settings import ContentSettings
from netra_api.content.reading.blocks import BlockType, ReadingBlock, Sentence
from netra_api.content.reading.postgres import AsyncReadingBlockRepository
from netra_api.content.retrieval.chunks import AsyncSearchChunkRepository, SearchChunk
from netra_api.content.sources.postgres import AsyncSourceRepository, SourceVersionConflictError
from netra_api.platform.auth_context import AuthContext
from netra_api.platform.errors import NetraError
from netra_api.db.models import SourceRow
from netra_api.platform.database import create_engine, create_session_factory


pytestmark = pytest.mark.integration


@pytest.fixture
async def db_session():
    url = os.environ.get("NETRA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("NETRA_TEST_DATABASE_URL (a disposable local database) is not set")
    engine = create_engine(url)
    factory: async_sessionmaker[AsyncSession] = create_session_factory(engine)
    async with factory() as session:
        yield session
    await engine.dispose()


def _auth(account_id):
    return AuthContext(account_id=account_id, session_id=uuid4(), request_id=uuid4(),
                       issued_at=datetime.now(timezone.utc))


async def _version(repo, auth, source_id, value):
    return await repo.create_version(
        auth, source_id, object_key=f"sources/{value}.pdf",
        content_hash=repo.content_hash_for(value.encode()), parser_name="pymupdf",
        parser_version="1.28.2", parser_config={"sort": True})


async def _ready(repo, auth, version_id):
    for stage in ("parsing", "blocks_built", "embedded", "projected"):
        await repo.mark_stage_complete(auth, version_id, stage)
    return await repo.mark_ready(auth, version_id)


async def _cleanup(session, source_id):
    await session.execute(delete(SourceRow).where(SourceRow.source_id == source_id))
    await session.commit()


@pytest.mark.asyncio
async def test_source_version_identity_lifecycle_and_activation(db_session):
    account = uuid4()
    auth = _auth(account)
    repo = AsyncSourceRepository(db_session)
    source = await repo.create_source(auth, "D1")
    try:
        version = await _version(repo, auth, source.source_id, "one")
        assert version.content_hash == repo.content_hash_for(b"one")
        assert version.parser_name == "pymupdf"
        with pytest.raises(NetraError):
            await repo.activate_version(auth, source.source_id, version.source_version_id, 0)
        ready = await _ready(repo, auth, version.source_version_id)
        assert ready.ingestion_state.value == "ready"
        active = await repo.activate_version(auth, source.source_id, version.source_version_id, 0)
        assert active.is_active is True
        assert active.ingestion_state.value == "active"
    finally:
        await _cleanup(db_session, source.source_id)


@pytest.mark.asyncio
async def test_failed_and_incomplete_versions_cannot_activate(db_session):
    auth = _auth(uuid4())
    repo = AsyncSourceRepository(db_session)
    source = await repo.create_source(auth, "D1 gates")
    try:
        failed = await _version(repo, auth, source.source_id, "failed")
        await repo.mark_failed(auth, failed.source_version_id)
        with pytest.raises(NetraError):
            await repo.activate_version(auth, source.source_id, failed.source_version_id, 0)

        incomplete = await _version(repo, auth, source.source_id, "incomplete")
        for stage in ("parsing", "blocks_built", "embedded"):
            await repo.mark_stage_complete(auth, incomplete.source_version_id, stage)
        with pytest.raises(NetraError):
            await repo.activate_version(auth, source.source_id, incomplete.source_version_id, 0)
    finally:
        await _cleanup(db_session, source.source_id)


@pytest.mark.asyncio
async def test_older_ready_version_cannot_activate_after_newer_version_exists(db_session):
    auth = _auth(uuid4())
    repo = AsyncSourceRepository(db_session)
    source = await repo.create_source(auth, "D1 freshness")
    try:
        older = await _version(repo, auth, source.source_id, "older")
        await _ready(repo, auth, older.source_version_id)
        newer = await _version(repo, auth, source.source_id, "newer")
        assert newer.version_number > older.version_number
        with pytest.raises(SourceVersionConflictError, match="obsolete"):
            await repo.activate_version(auth, source.source_id, older.source_version_id, 0)
    finally:
        await _cleanup(db_session, source.source_id)


@pytest.mark.asyncio
async def test_activation_deactivates_previous_version_and_replay_is_idempotent(db_session):
    auth = _auth(uuid4())
    repo = AsyncSourceRepository(db_session)
    source = await repo.create_source(auth, "D4 activation")
    try:
        first = await _version(repo, auth, source.source_id, "first")
        await _ready(repo, auth, first.source_version_id)
        active_first = await repo.activate_version(auth, source.source_id, first.source_version_id, 0)
        assert active_first.is_active

        second = await _version(repo, auth, source.source_id, "second")
        await _ready(repo, auth, second.source_version_id)
        active_second = await repo.activate_version(auth, source.source_id, second.source_version_id, 1)
        assert active_second.is_active
        assert (await repo.get_version(auth, first.source_version_id)).is_active is False
        assert (await repo.get_active_version(auth, source.source_id)).source_version_id == second.source_version_id

        replay = await repo.activate_version(auth, source.source_id, second.source_version_id, 2)
        assert replay.is_active
        # A redelivered activation job still carries the expectation it was
        # enqueued with (the previously active version number).
        redelivered = await repo.activate_version_internal(source.source_id, second.source_version_id, 1)
        assert redelivered.is_active
        assert len([version for version in await repo.list_versions(auth, source.source_id) if version.is_active]) == 1
    finally:
        await _cleanup(db_session, source.source_id)


@pytest.mark.asyncio
async def test_chunk_block_references_must_belong_to_same_source_version(db_session):
    auth = _auth(uuid4())
    sources = AsyncSourceRepository(db_session)
    source = await sources.create_source(auth, "D1 provenance")
    try:
        first = await _version(sources, auth, source.source_id, "first")
        second = await _version(sources, auth, source.source_id, "second")
        block = ReadingBlock(block_id=uuid4(), source_version_id=second.source_version_id, ordinal=0,
                             block_type=BlockType.PARAGRAPH,
                             sentences=[Sentence(sentence_id=uuid4(), ordinal=0, text="canonical")])
        await AsyncReadingBlockRepository(db_session).replace_blocks(second.source_version_id, [block])
        chunk = SearchChunk(source_version_id=first.source_version_id, text="cross-version",
                            block_ids=[block.block_id], embedding_version="pending")
        with pytest.raises(ValueError, match="source version"):
            await AsyncSearchChunkRepository(db_session).replace_chunks(first.source_version_id, [chunk])
    finally:
        await _cleanup(db_session, source.source_id)
