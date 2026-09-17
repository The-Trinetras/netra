"""Canonical search-chunk to Gemini-to-Pinecone projection service."""

from __future__ import annotations

import math
from typing import Protocol
from uuid import UUID

from netra_api.content.providers.pinecone import VectorIndexProvider
from netra_api.content.retrieval.chunks import SearchChunkProjection
from netra_api.content.retrieval.embeddings import EmbeddingProvider, EmbeddingResult, EmbeddingSpec
from netra_api.config import Settings


class ProjectionChunkReader(Protocol):
    async def list_projection_chunks(self, source_version_id: UUID) -> list[SearchChunkProjection]:
        ...


class PineconeProjectionService:
    """Projects only validated canonical chunks; it never writes PostgreSQL."""

    def __init__(self, chunks: ProjectionChunkReader, embedder: EmbeddingProvider,
                 index: VectorIndexProvider, settings: Settings | None = None) -> None:
        self.chunks, self.embedder, self.index = chunks, embedder, index
        self.settings = settings or Settings()

    async def project_source_version(self, source_version_id: UUID) -> int:
        chunks = await self.chunks.list_projection_chunks(source_version_id)
        if not chunks:
            return 0
        if all(chunk.embedding is not None and chunk.embedding_spec is not None for chunk in chunks):
            results = [EmbeddingResult(vector=chunk.embedding, specification=chunk.embedding_spec) for chunk in chunks]
        elif any(chunk.embedding is not None or chunk.embedding_spec is not None for chunk in chunks):
            raise ValueError("canonical chunks have incomplete embedding state")
        else:
            results = await self.embedder.embed_batch([chunk.text for chunk in chunks])
        if len(results) != len(chunks):
            raise ValueError("embedding count does not match canonical chunk count")
        expected = EmbeddingSpec.from_settings(self.settings)
        vectors = []
        for chunk, result in zip(chunks, results):
            if result.specification != expected or len(result.vector) != expected.dimension:
                raise ValueError("embedding specification is incompatible with projection configuration")
            if not all(isinstance(value, (int, float)) and math.isfinite(float(value)) for value in result.vector):
                raise ValueError("embedding vector contains invalid values")
            metadata = {
                **chunk.metadata,
                "account_id": str(chunk.account_id), "source_id": str(chunk.source_id),
                "source_version_id": str(chunk.source_version_id), "chunk_id": str(chunk.chunk_id),
                "embedding_model": result.specification.model,
                "embedding_dimension": result.specification.dimension,
                "embedding_spec": result.specification.version,
            }
            vectors.append((str(chunk.chunk_id), result.vector, metadata))
        await self.index.upsert(self.settings.pinecone_namespace, vectors)
        return len(vectors)
