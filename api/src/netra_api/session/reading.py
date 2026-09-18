"""Session-side access to M2's reading positions, blocks and source versions.

The Session service stores *where* the student is; M2's content services
resolve *what* a position points at and what the next/previous unit is
(content/reading/positions.py). This adapter consumes those reviewed
protocols and never invents positions: every destination comes from
ReadingPositionRepository, every sentence text from ReadingBlockRepository,
and source access/pinning is checked through SourceRepository.get_version
before content is delivered.

Teammate implementations may raise their own exception types. Authorization
and other NetraErrors propagate unchanged; anything else is reported as
RESOURCE_UNAVAILABLE rather than leaking internals or pretending success.
"""

from __future__ import annotations

import logging
from typing import Any, Optional
from uuid import UUID

from netra_api.content.reading.blocks import ReadingBlock
from netra_api.content.reading.positions import BlockLocator
from netra_api.platform.auth_context import AuthContext
from netra_api.platform.awaitables import maybe_await
from netra_api.platform.errors import NetraError, ResourceUnavailableError
from netra_api.session.modes import NavigationUnit
from netra_api.session.state import ReadingPosition

logger = logging.getLogger(__name__)


def _uuid(value: Optional[str]) -> UUID:
    if value is None:
        raise ResourceUnavailableError("reading position is incomplete")
    try:
        return UUID(value)
    except ValueError as exc:
        raise ResourceUnavailableError("stored reading position is not a valid reference") from exc


def to_locator(position: ReadingPosition) -> BlockLocator:
    return BlockLocator(
        source_version_id=_uuid(position.source_version_id),
        block_id=_uuid(position.current_block_id),
        sentence_id=_uuid(position.current_sentence_id) if position.current_sentence_id else None,
    )


def to_position(locator: BlockLocator) -> ReadingPosition:
    return ReadingPosition(
        source_version_id=str(locator.source_version_id),
        current_block_id=str(locator.block_id),
        current_sentence_id=str(locator.sentence_id) if locator.sentence_id else None,
    )


class ReadingAccess:
    def __init__(self, positions: Any, blocks: Any, sources: Any) -> None:
        self._positions = positions
        self._blocks = blocks
        self._sources = sources

    async def _call(self, operation: str, function: Any, *args: Any) -> Any:
        try:
            return await maybe_await(function(*args))
        except NetraError:
            raise
        except Exception as exc:  # teammate boundary: never leak, never succeed
            logger.warning("reading dependency %s failed: %s", operation, type(exc).__name__)
            raise ResourceUnavailableError("reading content is temporarily unavailable") from exc

    async def assert_source_access(self, auth: AuthContext, source_version_id: str) -> None:
        """Authorize the pinned source version for this account.

        SourceRepository.get_version raises AuthorizationError for another
        account's version; that propagates as AUTHORIZATION_DENIED.
        """

        await self._call("get_version", self._sources.get_version, auth, _uuid(source_version_id))

    async def first(self, source_version_id: str) -> ReadingPosition:
        locator = await self._call("first", self._positions.first, _uuid(source_version_id))
        return to_position(locator)

    async def resolve(self, position: ReadingPosition) -> ReadingPosition:
        locator = await self._call("resolve", self._positions.resolve, to_locator(position))
        return to_position(locator)

    async def step(self, position: ReadingPosition, unit: NavigationUnit, *, forward: bool) -> Optional[ReadingPosition]:
        locator = to_locator(position)
        if forward:
            result = await self._call("next", self._positions.next, locator, unit)
        else:
            result = await self._call("previous", self._positions.previous, locator, unit)
        return to_position(result) if result is not None else None

    async def block(self, position: ReadingPosition) -> ReadingBlock:
        return await self._call(
            "get_block", self._blocks.get_block, _uuid(position.source_version_id), _uuid(position.current_block_id)
        )

    async def block_count(self, source_version_id: str) -> int:
        return int(await self._call("count_blocks", self._blocks.count_blocks, _uuid(source_version_id)))
