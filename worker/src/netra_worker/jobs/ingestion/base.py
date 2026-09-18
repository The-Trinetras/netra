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
    async def active_version_number_internal(self, source_id: UUID) -> int: ...


class IngestionJobPayload(JobPayload):
    """Fields every ingestion pipeline stage payload carries."""

    source_id: UUID
    source_version_id: UUID


class StageScheduler(Protocol):
    """Enqueues the next pipeline stage; idempotent by ``idempotency_key``.

    Satisfied by the job repository (``AsyncJobRepository.enqueue``). Each stage
    schedules its successor only after committing its own stage record, and a
    redelivered stage that is already complete schedules it again. A crash
    between the two therefore re-delivers this stage instead of stalling the
    pipeline, and the successor's unique key absorbs the repeat.
    """

    async def enqueue(self, job_type: str, payload: JobPayload, idempotency_key: str): ...


def stage_key(job_type: str, source_version_id: UUID) -> str:
    """The one idempotency key for a pipeline stage of one source version."""

    return f"{job_type}:{source_version_id}"


async def schedule_next(scheduler: "StageScheduler | None", job_type: str, payload: JobPayload) -> None:
    """Enqueue the successor stage when the pipeline is wired (tests may omit it)."""

    if scheduler is not None:
        await scheduler.enqueue(job_type, payload, payload.idempotency_key)
