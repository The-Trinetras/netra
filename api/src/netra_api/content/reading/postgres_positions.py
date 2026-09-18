"""PostgreSQL ``ReadingPositionRepository``: deterministic navigation within one version.

Resolves and steps locators over a source version's immutable reading blocks
(``netra_api.content.reading.positions``). It never stores "where the student
is" (the Session service owns that) and never crosses into another source
version, so a pinned session stays on its version. Access to the version is
authorized by the caller through ``SourceRepository.get_version`` before any
content is delivered (M1 ``ReadingAccess``).

Step semantics, one per ``NavigationUnit``:

- SENTENCE: the next/previous sentence, crossing block boundaries.
- BLOCK: the first sentence of the next/previous block.
- HEADING / FIGURE / EQUATION: the first sentence of the next/previous block of
  that type (heading, figure reference, equation reference).

A block without sentences is addressed as a whole (``sentence_id=None``).
``None`` means start/end of document; a missing block or sentence is a stale
reference, never a guessed nearby position.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from netra_api.content.reading.blocks import BlockType
from netra_api.content.reading.positions import BlockLocator
from netra_api.db.models import ReadingBlockRow
from netra_api.platform.errors import ResourceUnavailableError, StaleRequestError
from netra_api.session.modes import NavigationUnit

_TARGET_KIND = {
    NavigationUnit.HEADING: BlockType.HEADING.value,
    NavigationUnit.FIGURE: BlockType.FIGURE_REFERENCE.value,
    NavigationUnit.EQUATION: BlockType.EQUATION_REFERENCE.value,
}


def _sentence_ids(row: ReadingBlockRow) -> list[UUID]:
    ordered = sorted(row.sentences or [], key=lambda item: item.get("ordinal", 0))
    return [UUID(str(item["sentence_id"])) for item in ordered]


def _first_of(row: ReadingBlockRow) -> BlockLocator:
    sentences = _sentence_ids(row)
    return BlockLocator(source_version_id=row.source_version_id, block_id=row.block_id,
                        sentence_id=sentences[0] if sentences else None)


def _last_of(row: ReadingBlockRow) -> BlockLocator:
    sentences = _sentence_ids(row)
    return BlockLocator(source_version_id=row.source_version_id, block_id=row.block_id,
                        sentence_id=sentences[-1] if sentences else None)


class AsyncReadingPositionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _block(self, locator: BlockLocator) -> ReadingBlockRow:
        row = (await self.session.execute(select(ReadingBlockRow).where(
            ReadingBlockRow.source_version_id == locator.source_version_id,
            ReadingBlockRow.block_id == locator.block_id))).scalar_one_or_none()
        if row is None:
            raise StaleRequestError("reading position no longer exists in this source version")
        if locator.sentence_id is not None and locator.sentence_id not in _sentence_ids(row):
            raise StaleRequestError("reading position no longer exists in this source version")
        return row

    async def _neighbour(self, row: ReadingBlockRow, *, forward: bool,
                         kind: Optional[str] = None) -> Optional[ReadingBlockRow]:
        sequence = ReadingBlockRow.sequence_id
        query = select(ReadingBlockRow).where(
            ReadingBlockRow.source_version_id == row.source_version_id,
            sequence > row.sequence_id if forward else sequence < row.sequence_id)
        if kind is not None:
            query = query.where(ReadingBlockRow.kind == kind)
        query = query.order_by(sequence.asc() if forward else sequence.desc()).limit(1)
        return (await self.session.execute(query)).scalar_one_or_none()

    async def resolve(self, locator: BlockLocator) -> BlockLocator:
        await self._block(locator)
        return locator

    async def first(self, source_version_id: UUID) -> BlockLocator:
        row = (await self.session.execute(select(ReadingBlockRow).where(
            ReadingBlockRow.source_version_id == source_version_id)
            .order_by(ReadingBlockRow.sequence_id).limit(1))).scalar_one_or_none()
        if row is None:
            raise ResourceUnavailableError("source version has no readable content")
        return _first_of(row)

    async def next(self, locator: BlockLocator,
                   unit: NavigationUnit = NavigationUnit.SENTENCE) -> Optional[BlockLocator]:
        row = await self._block(locator)
        if unit == NavigationUnit.SENTENCE and locator.sentence_id is not None:
            sentences = _sentence_ids(row)
            position = sentences.index(locator.sentence_id)
            if position + 1 < len(sentences):
                return locator.model_copy(update={"sentence_id": sentences[position + 1]})
        following = await self._neighbour(row, forward=True, kind=_TARGET_KIND.get(unit))
        return _first_of(following) if following is not None else None

    async def previous(self, locator: BlockLocator,
                       unit: NavigationUnit = NavigationUnit.SENTENCE) -> Optional[BlockLocator]:
        row = await self._block(locator)
        if unit == NavigationUnit.SENTENCE:
            sentences = _sentence_ids(row)
            if locator.sentence_id is not None:
                position = sentences.index(locator.sentence_id)
                if position > 0:
                    return locator.model_copy(update={"sentence_id": sentences[position - 1]})
            preceding = await self._neighbour(row, forward=False)
            return _last_of(preceding) if preceding is not None else None
        preceding = await self._neighbour(row, forward=False, kind=_TARGET_KIND.get(unit))
        return _first_of(preceding) if preceding is not None else None


__all__ = ["AsyncReadingPositionRepository"]
