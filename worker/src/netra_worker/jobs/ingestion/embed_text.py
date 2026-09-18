"""Worker stage that embeds canonical search chunks through Gemini."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from pydantic import Field

from netra_api.content.settings import ContentSettings
from netra_api.content.retrieval.chunks import SearchChunk
from netra_api.content.retrieval.embeddings import EmbeddingProvider, EmbeddingSpec
from netra_api.content.sources.models import SourceVersionIngestionState
from netra_api.content.telemetry import instrument_stage

from netra_worker.jobs.ingestion.base import (
    IngestionJobPayload,
    IngestionVersionStore,
    StageScheduler,
    schedule_next,
    stage_key,
)
from netra_worker.runtime.errors import PermanentJobError


class EmbedTextPayload(IngestionJobPayload):
    block_ids: list[UUID] = Field(default_factory=list)


class EmbeddingChunkStore(Protocol):
    async def list_for_embedding(self, source_version_id: UUID) -> list[SearchChunk]: ...
    async def save_embedding(self, chunk_id: UUID, vector: list[float], specification: EmbeddingSpec) -> None: ...


class EmbedTextJob:
    def __init__(self, versions: IngestionVersionStore, chunks: EmbeddingChunkStore,
                 embedder: EmbeddingProvider, settings: ContentSettings | None = None,
                 scheduler: StageScheduler | None = None) -> None:
        self.versions, self.chunks, self.embedder = versions, chunks, embedder
        self.settings = settings or ContentSettings()
        self.scheduler = scheduler

    @instrument_stage("embed_chunks")
    async def handle(self, payload: EmbedTextPayload) -> None:
        version = await self.versions.get_version_internal(payload.source_version_id)
        if version.source_id != payload.source_id:
            raise PermanentJobError("job payload does not match the source version")
        if "embedded" in version.completed_stages:
            await self._schedule_projection(payload)
            return
        if version.ingestion_state not in {SourceVersionIngestionState.BLOCKS_BUILT,
                                           SourceVersionIngestionState.EMBEDDED}:
            raise PermanentJobError("source version is not ready for embedding")
        chunks = [chunk for chunk in await self.chunks.list_for_embedding(payload.source_version_id)
                  if chunk.embedding is None]
        # Chunks already embedded by an interrupted attempt are kept, so a
        # retry pays only for the remainder.
        results = await self.embedder.embed_batch([chunk.text for chunk in chunks]) if chunks else []
        expected = EmbeddingSpec.from_settings(self.settings)
        if len(results) != len(chunks) or any(
                result.specification != expected or len(result.vector) != expected.dimension
                for result in results):
            # A model/dimension mismatch is configuration, not a transient fault.
            raise PermanentJobError("embedding response is incompatible with projection configuration")
        for chunk, result in zip(chunks, results):
            await self.chunks.save_embedding(chunk.chunk_id, result.vector, result.specification)
        await self.versions.mark_stage_complete_internal(payload.source_version_id, "embedded")
        await self._schedule_projection(payload)

    async def _schedule_projection(self, payload: EmbedTextPayload) -> None:
        from netra_worker.jobs.search_projection.pinecone import SearchProjectionPayload

        await schedule_next(self.scheduler, "search_projection", SearchProjectionPayload(
            idempotency_key=stage_key("search_projection", payload.source_version_id),
            source_version_id=payload.source_version_id))
