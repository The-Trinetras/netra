"""Worker stage that embeds canonical search chunks through Gemini."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from pydantic import Field

from netra_api.config import Settings
from netra_api.content.retrieval.chunks import SearchChunk
from netra_api.content.retrieval.embeddings import EmbeddingProvider, EmbeddingSpec
from netra_api.content.sources.models import SourceVersionIngestionState
from netra_api.platform.observability import instrument_stage

from netra_worker.jobs.ingestion.base import IngestionJobPayload, IngestionVersionStore


class EmbedTextPayload(IngestionJobPayload):
    block_ids: list[UUID] = Field(default_factory=list)


class EmbeddingChunkStore(Protocol):
    async def list_for_embedding(self, source_version_id: UUID) -> list[SearchChunk]: ...
    async def save_embedding(self, chunk_id: UUID, vector: list[float], specification: EmbeddingSpec) -> None: ...


class EmbedTextJob:
    def __init__(self, versions: IngestionVersionStore, chunks: EmbeddingChunkStore,
                 embedder: EmbeddingProvider, settings: Settings | None = None) -> None:
        self.versions, self.chunks, self.embedder = versions, chunks, embedder
        self.settings = settings or Settings()

    @instrument_stage("embed_chunks")
    async def handle(self, payload: EmbedTextPayload) -> None:
        version = await self.versions.get_version_internal(payload.source_version_id)
        if version.source_id != payload.source_id or version.ingestion_state not in {
                SourceVersionIngestionState.BLOCKS_BUILT, SourceVersionIngestionState.EMBEDDED}:
            raise RuntimeError("source version is not ready for embedding")
        chunks = [chunk for chunk in await self.chunks.list_for_embedding(payload.source_version_id)
                  if chunk.embedding is None]
        results = await self.embedder.embed_batch([chunk.text for chunk in chunks])
        expected = EmbeddingSpec.from_settings(self.settings)
        if len(results) != len(chunks) or any(
                result.specification != expected or len(result.vector) != expected.dimension
                for result in results):
            raise ValueError("embedding response is incompatible with projection configuration")
        for chunk, result in zip(chunks, results):
            await self.chunks.save_embedding(chunk.chunk_id, result.vector, result.specification)
        await self.versions.mark_stage_complete_internal(payload.source_version_id, "embedded")
