"""Search projection job: rebuild derived Pinecone vectors from PostgreSQL.

Pinecone is a derived, rebuildable projection (CLAUDE.md "Data
authority"); a failed projection write must never roll back the
PostgreSQL mutation it followed from. This job drains outbox events
(see netra_worker.runtime.outbox.OutboxRepository) and would call a
vector-index provider shaped like
netra_api.content.providers.pinecone.VectorIndexProvider — no Pinecone
SDK call is implemented here.
"""

from __future__ import annotations

from uuid import UUID

from netra_worker.runtime.job_repository import JobPayload


class SearchProjectionPayload(JobPayload):
    source_version_id: UUID


class SearchProjectionJob:
    """Structurally implements netra_worker.runtime.job_repository.JobHandler[SearchProjectionPayload].

    TODO: inject a VectorIndexProvider-shaped adapter and the reading-block
    read side once provider wiring for worker/ is decided.
    """

    async def handle(self, payload: SearchProjectionPayload) -> None:
        raise NotImplementedError("TODO: search_projection — no Pinecone SDK call implemented")
