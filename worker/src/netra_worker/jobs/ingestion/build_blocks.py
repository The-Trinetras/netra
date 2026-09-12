"""Ingestion stage: turn parsed output into persisted ReadingBlocks.

Writes through a store shaped like
netra_api.content.reading.repository.ReadingBlockRepository.replace_blocks
(not imported directly since worker/ and api/ are separate
deployables). This job must be idempotent since a retry may re-run
after a partial write (CLAUDE.md "Background jobs").
"""

from __future__ import annotations

from netra_worker.jobs.ingestion.base import IngestionJobPayload


class BuildBlocksPayload(IngestionJobPayload):
    parsed_object_key: str


class BuildBlocksJob:
    """Structurally implements netra_worker.runtime.job_repository.JobHandler[BuildBlocksPayload].

    TODO: inject the ReadingBlockRepository-shaped store.
    """

    async def handle(self, payload: BuildBlocksPayload) -> None:
        raise NotImplementedError("TODO: build_blocks — replace_blocks call not implemented")
