"""Async persistence for immutable-version reading blocks."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from netra_api.db.transactions import close_read_only_transaction

from netra_api.content.reading.blocks import BlockType, ReadingBlock, Sentence
from netra_api.db.models import ReadingBlockRow


def _domain(row: ReadingBlockRow) -> ReadingBlock:
    return ReadingBlock(block_id=row.block_id, source_version_id=row.source_version_id,
                        ordinal=row.sequence_id, block_type=BlockType(row.kind),
                        sentences=[Sentence.model_validate(item) for item in row.sentences],
                        locator=(row.structured_location or {}).get("locator"),
                        page_index=row.page_index, printed_page=row.printed_page,
                        structured_location=row.structured_location or {})


class AsyncReadingBlockRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _close_read_transaction(self) -> None:
        await close_read_only_transaction(self.session)

    async def get_block(self, source_version_id: UUID, block_id: UUID) -> ReadingBlock | None:
        row = (await self.session.execute(select(ReadingBlockRow).where(
            ReadingBlockRow.source_version_id == source_version_id, ReadingBlockRow.block_id == block_id))).scalar_one_or_none()
        return _domain(row) if row else None

    async def list_blocks(self, source_version_id: UUID) -> list[ReadingBlock]:
        rows = (await self.session.execute(select(ReadingBlockRow).where(
            ReadingBlockRow.source_version_id == source_version_id).order_by(ReadingBlockRow.sequence_id))).scalars()
        return [_domain(row) for row in rows]

    async def get_block_at_ordinal(self, source_version_id: UUID, ordinal: int) -> ReadingBlock | None:
        row = (await self.session.execute(select(ReadingBlockRow).where(
            ReadingBlockRow.source_version_id == source_version_id, ReadingBlockRow.sequence_id == ordinal))).scalar_one_or_none()
        return _domain(row) if row else None

    async def count_blocks(self, source_version_id: UUID) -> int:
        return int((await self.session.execute(select(func.count()).select_from(ReadingBlockRow).where(
            ReadingBlockRow.source_version_id == source_version_id))).scalar_one())

    async def replace_blocks(self, source_version_id: UUID, blocks: list[ReadingBlock]) -> None:
        if any(block.source_version_id != source_version_id for block in blocks):
            raise ValueError("all blocks must belong to the requested source version")
        ordinals = [block.ordinal for block in blocks]
        if len(ordinals) != len(set(ordinals)) or sorted(ordinals) != list(range(len(blocks))):
            raise ValueError("block ordinals must be unique and contiguous")
        await self._close_read_transaction()
        async with self.session.begin():
            await self.session.execute(delete(ReadingBlockRow).where(ReadingBlockRow.source_version_id == source_version_id))
            self.session.add_all([ReadingBlockRow(block_id=b.block_id, source_version_id=source_version_id,
                sequence_id=b.ordinal, kind=b.block_type.value, text=" ".join(s.text for s in b.sentences),
                page_index=b.page_index, printed_page=b.printed_page,
                structured_location=b.structured_location or ({"locator": b.locator} if b.locator else {}),
                sentences=[s.model_dump(mode="json") for s in b.sentences]) for b in blocks])
