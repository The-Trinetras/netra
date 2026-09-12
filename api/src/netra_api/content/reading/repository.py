"""Reading-block persistence interface.

PostgreSQL is authoritative (CLAUDE.md "Data authority": "source
versions/reading blocks -> Content/Ingestion service in PostgreSQL").
The worker's build_blocks ingestion stage is the only writer; the API
reads through this Protocol to serve reading, retrieval, and
navigation. A concrete PostgreSQL-backed implementation is added
alongside the reading_blocks migration, not here.
"""

from __future__ import annotations

from typing import List, Optional, Protocol
from uuid import UUID

from netra_api.content.reading.blocks import ReadingBlock


class ReadingBlockRepository(Protocol):
    """Typed contract for reading and bulk-writing a source version's blocks."""

    def get_block(self, source_version_id: UUID, block_id: UUID) -> ReadingBlock:
        ...

    def list_blocks(self, source_version_id: UUID) -> List[ReadingBlock]:
        ...

    def get_block_at_ordinal(self, source_version_id: UUID, ordinal: int) -> Optional[ReadingBlock]:
        ...

    def count_blocks(self, source_version_id: UUID) -> int:
        ...

    def replace_blocks(self, source_version_id: UUID, blocks: List[ReadingBlock]) -> None:
        """Idempotently (re)write all blocks for source_version_id.

        Called by the build_blocks ingestion job, which may run more
        than once for the same source_version_id under at-least-once
        execution (CLAUDE.md "Background jobs").
        """
        ...
