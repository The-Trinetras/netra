"""Ingestion stage: request embeddings for a source version's blocks.

Embedding generation is an external provider call; this stage must not
hold a PostgreSQL transaction open while awaiting it (CLAUDE.md
"Background jobs"). Produced vectors are handed off for search
projection (see netra_worker.runtime.outbox and
netra_worker.jobs.search_projection), not upserted to Pinecone directly
here.
"""

from __future__ import annotations

from typing import List
from uuid import UUID

from netra_worker.jobs.ingestion.base import IngestionJobPayload


class EmbedTextPayload(IngestionJobPayload):
    block_ids: List[UUID]


class EmbedTextJob:
    """Structurally implements netra_worker.runtime.job_repository.JobHandler[EmbedTextPayload]."""

    async def handle(self, payload: EmbedTextPayload) -> None:
        raise NotImplementedError("TODO: embed_text — no embedding provider call implemented")
