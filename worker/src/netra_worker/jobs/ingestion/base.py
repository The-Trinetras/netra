"""Shared contract for document-ingestion pipeline stages.

Document ingestion is an explicitly-listed NON-agent bounded workflow
(CLAUDE.md "Architecture: only two agents"). Each stage below is a
netra_worker.runtime.job_repository.JobHandler: idempotent, and safe to
re-run under at-least-once execution (CLAUDE.md "Background jobs").
This module only fixes the payload fields every ingestion stage shares.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from netra_worker.runtime.job_repository import JobPayload


class IngestionVersionStore(Protocol):
    async def get_version_internal(self, source_version_id: UUID): ...
    async def mark_stage_complete_internal(self, source_version_id: UUID, stage: str): ...
    async def mark_ready_internal(self, source_version_id: UUID): ...
    async def mark_failed_internal(self, source_version_id: UUID): ...


class IngestionJobPayload(JobPayload):
    """Fields every ingestion pipeline stage payload carries."""

    source_id: UUID
    source_version_id: UUID
