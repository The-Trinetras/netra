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
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class SourceVersionStatus(str, Enum):
    """Lifecycle of one ingestion pipeline run for a source version."""

    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
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
