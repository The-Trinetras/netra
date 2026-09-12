"""Ingestion stage: activate a fully-built SourceVersion.

Activation flips SourceVersion.is_active in a single PostgreSQL
transaction (CLAUDE.md "Source versions must support activation/version
pinning") through a store shaped like
netra_api.content.sources.repository.SourceRepository.activate_version.
This is the terminal ingestion stage; it enqueues an outbox event for
search projection rather than calling Pinecone itself (CLAUDE.md "Use
an outbox when a committed PostgreSQL mutation requires a later
projection/update").
"""

from __future__ import annotations

from netra_worker.jobs.ingestion.base import IngestionJobPayload


class ActivateVersionPayload(IngestionJobPayload):
    expected_version_number: int


class ActivateVersionJob:
    """Structurally implements netra_worker.runtime.job_repository.JobHandler[ActivateVersionPayload].

    TODO: inject the SourceRepository-shaped store and
    netra_worker.runtime.outbox.OutboxRepository.
    """

    async def handle(self, payload: ActivateVersionPayload) -> None:
        raise NotImplementedError("TODO: activate_version — activation + outbox enqueue not implemented")
