"""Source and source-version domain models.

PostgreSQL is authoritative for these records (CLAUDE.md "Data
authority": "source versions/reading blocks -> Content/Ingestion
service in PostgreSQL"). A Source is a logical document a student has
added; a SourceVersion is one immutable snapshot produced by one
ingestion pipeline run. Exactly zero or one SourceVersion per Source is
active at a time (CLAUDE.md "Source versions must support
activation/version pinning") — activation is how re-ingestion rolls
forward without breaking a session mid-read.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class SourceVersionStatus(str, Enum):
    """Lifecycle of one ingestion pipeline run for a source version."""

    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class SourceVersionIngestionState(str, Enum):
    """Ordered ingestion gate for one immutable source version."""

    PENDING = "pending"
    PARSING = "parsing"
    BLOCKS_BUILT = "blocks_built"
    EMBEDDED = "embedded"
    PROJECTED = "projected"
    READY = "ready"
    ACTIVE = "active"
    FAILED = "failed"


class Source(BaseModel):
    """A logical document a student has added to their library."""

    source_id: UUID
    account_id: UUID
    title: str
    created_at: datetime


class SourceVersion(BaseModel):
    """One immutable parsed snapshot of a Source.

    version_number is monotonically increasing per source_id.
    is_active marks the version currently served for reading and
    retrieval; SourceRepository.activate_version is the only place this
    flips (see netra_api.content.sources.repository).
    """

    source_version_id: UUID
    source_id: UUID
    version_number: int = Field(ge=1)
    status: SourceVersionStatus = SourceVersionStatus.PENDING
    is_active: bool = False
    created_at: datetime
    activated_at: Optional[datetime] = None
    object_key: Optional[str] = None
    content_hash: Optional[str] = None
    parser_name: Optional[str] = None
    parser_version: Optional[str] = None
    parser_config: dict[str, Any] = Field(default_factory=dict)
    ingestion_state: SourceVersionIngestionState = SourceVersionIngestionState.PENDING
    completed_stages: list[str] = Field(default_factory=list)

    def is_activation_eligible(self) -> bool:
        """Return whether all canonical and semantic gates have completed."""
        required = {"parsing", "blocks_built", "embedded", "projected"}
        return (
            self.ingestion_state == SourceVersionIngestionState.READY
            and self.status == SourceVersionStatus.READY
            and required.issubset(self.completed_stages)
            and bool(self.object_key and self.content_hash and self.parser_name and self.parser_version)
        )
