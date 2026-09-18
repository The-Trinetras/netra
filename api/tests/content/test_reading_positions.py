"""Reading-position navigation over one immutable source version."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from netra_api.content.reading.blocks import BlockType, ReadingBlock, Sentence
from netra_api.content.reading.positions import BlockLocator
from netra_api.content.reading.postgres_positions import AsyncReadingPositionRepository, _sentence_ids
from netra_api.db.models import ReadingBlockRow
from netra_api.platform.errors import ResourceUnavailableError, StaleRequestError
from netra_api.session.modes import NavigationUnit as Unit


def test_sentences_are_ordered_by_ordinal_not_storage_order():
    first, second = uuid4(), uuid4()
    row = ReadingBlockRow(sentences=[{"sentence_id": str(second), "ordinal": 1, "text": "b"},
                                     {"sentence_id": str(first), "ordinal": 0, "text": "a"}])
    assert _sentence_ids(row) == [first, second]


@pytest.fixture
async def session():
    url = os.environ.get("NETRA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("NETRA_TEST_DATABASE_URL (a disposable local database) is not set")
    from netra_api.platform.database import create_engine, create_session_factory

    engine = create_engine(url)
    try:
        async with create_session_factory(engine)() as db_session:
            yield db_session
    finally:
        await engine.dispose()


def _block(version_id, ordinal, kind, *texts):
    return ReadingBlock(block_id=uuid4(), source_version_id=version_id, ordinal=ordinal, block_type=kind,
                        sentences=[Sentence(sentence_id=uuid4(), ordinal=i, text=t) for i, t in enumerate(texts)])


@pytest.mark.integration
async def test_navigation_by_every_unit_within_one_version(session):
    from sqlalchemy import delete
    from netra_api.content.reading.postgres import AsyncReadingBlockRepository
    from netra_api.db.models import SourceRow, SourceVersionRow

    now = datetime.now(timezone.utc)
    source_id, version_id = uuid4(), uuid4()
    async with session.begin():
        session.add(SourceRow(source_id=source_id, account_id=uuid4(), title="nav", created_at=now))
        await session.flush()
        session.add(SourceVersionRow(source_version_id=version_id, source_id=source_id, version_number=1,
                                     status="ready", is_active=True, created_at=now, parser_config={},
                                     ingestion_state="active", completed_stages=[]))
    blocks = [
        _block(version_id, 0, BlockType.HEADING, "Ohm's law"),
        _block(version_id, 1, BlockType.PARAGRAPH, "V equals I R.", "Units are volts."),
        _block(version_id, 2, BlockType.FIGURE_REFERENCE),  # no sentences: block-level locator
        _block(version_id, 3, BlockType.EQUATION_REFERENCE, "V = I R"),
        _block(version_id, 4, BlockType.HEADING, "Power"),
    ]
    try:
        await AsyncReadingBlockRepository(session).replace_blocks(version_id, blocks)
        positions = AsyncReadingPositionRepository(session)

        def at(block, sentence=0):
            sid = block.sentences[sentence].sentence_id if block.sentences else None
            return BlockLocator(source_version_id=version_id, block_id=block.block_id, sentence_id=sid)

        assert await positions.first(version_id) == at(blocks[0])
        # sentence steps cross block boundaries in both directions
        assert await positions.next(at(blocks[1]), Unit.SENTENCE) == at(blocks[1], 1)
        assert await positions.next(at(blocks[1], 1), Unit.SENTENCE) == at(blocks[2])
        assert await positions.previous(at(blocks[1]), Unit.SENTENCE) == at(blocks[0])
        assert await positions.previous(at(blocks[3]), Unit.SENTENCE) == at(blocks[2])
        # structural steps land on the first sentence of the target block
        assert await positions.next(at(blocks[1], 1), Unit.BLOCK) == at(blocks[2])
        assert await positions.next(at(blocks[0]), Unit.HEADING) == at(blocks[4])
        assert await positions.next(at(blocks[0]), Unit.FIGURE) == at(blocks[2])
        assert await positions.previous(at(blocks[4]), Unit.EQUATION) == at(blocks[3])
        # document edges are None, never a wrapped or guessed position
        assert await positions.next(at(blocks[4]), Unit.SENTENCE) is None
        assert await positions.previous(at(blocks[0]), Unit.HEADING) is None
        # a locator from another version or a missing sentence is stale
        with pytest.raises(StaleRequestError):
            await positions.resolve(BlockLocator(source_version_id=uuid4(), block_id=blocks[0].block_id))
        with pytest.raises(StaleRequestError):
            await positions.resolve(BlockLocator(source_version_id=version_id, block_id=blocks[1].block_id,
                                                 sentence_id=uuid4()))
        with pytest.raises(ResourceUnavailableError):
            await positions.first(uuid4())
    finally:
        if session.in_transaction():
            await session.commit()
        async with session.begin():
            await session.execute(delete(SourceRow).where(SourceRow.source_id == source_id))
