"""Reading-block domain models.

A ReadingBlock is one structural unit (paragraph, heading, list item,
...) of a SourceVersion's parsed text, split into sentences for
sentence-level playback tracking (CLAUDE.md "Session rules": a session
tracks "current sentence"). Figures, equations, and video are handled
by separate bounded tools (CLAUDE.md "Architecture: only two agents"
lists figure/equation/video processing as NOT agents) and are
referenced here by locator only, never inlined.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class BlockType(str, Enum):
    PARAGRAPH = "paragraph"
    HEADING = "heading"
    LIST_ITEM = "list_item"
    TABLE = "table"
    FIGURE_REFERENCE = "figure_reference"
    EQUATION_REFERENCE = "equation_reference"
    CODE = "code"


class Sentence(BaseModel):
    """One sentence-level playback/navigation unit within a ReadingBlock."""

    sentence_id: UUID
    ordinal: int = Field(ge=0)
    text: str


class ReadingBlock(BaseModel):
    """One structural unit of a SourceVersion's parsed, readable text."""

    block_id: UUID
    source_version_id: UUID
    ordinal: int = Field(ge=0)
    block_type: BlockType
    sentences: List[Sentence] = Field(default_factory=list)
    locator: Optional[str] = None
    """Page/timestamp/section locator used for evidence citation, when available."""
    page_index: int | None = None
    printed_page: str | None = None
    structured_location: dict[str, Any] = Field(default_factory=dict)
