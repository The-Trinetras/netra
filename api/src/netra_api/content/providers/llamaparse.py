"""Document-parser provider interface.

CLAUDE.md "Provider adapters": business logic must not depend on a
provider SDK's response objects directly. This interface is the
boundary the worker's parse_document ingestion stage depends on; no
LlamaParse (or any other parser) SDK call is implemented here, so
swapping providers never touches job logic. No provider is wired up in
this scaffold.
"""

from __future__ import annotations

from typing import Any, List, Protocol

from pydantic import BaseModel


class ParsedBlock(BaseModel):
    """One raw structural unit produced by a parser, before block-building."""

    text: str
    block_type_hint: str
    """Provider-reported type (e.g. "paragraph", "heading"); mapped onto
    netra_api.content.reading.blocks.BlockType by the build_blocks
    ingestion stage, not here."""
    page_index: int | None = None
    printed_page: str | None = None
    structured_location: dict[str, Any] = {}
    parent_id: str = "document"
    parent_path: tuple[str, ...] = ()
    source_metadata: dict[str, Any] = {}


class DocumentParserProvider(Protocol):
    """Typed contract for turning raw source bytes into ParsedBlocks."""

    async def parse(self, object_key: str, content_type: str) -> List[ParsedBlock]:
        ...
