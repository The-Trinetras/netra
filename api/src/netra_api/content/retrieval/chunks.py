"""Retrieval-oriented search chunks and their canonical persistence."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from netra_api.db.models import ReadingBlockRow, SearchChunkRow, SourceRow, SourceVersionRow
from netra_api.content.retrieval.embeddings import EmbeddingSpec


class SearchChunk(BaseModel):
    chunk_id: UUID = Field(default_factory=uuid4)
    source_version_id: UUID
    text: str
    block_ids: list[UUID]
    embedding_version: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    embedding: list[float] | None = None
    embedding_spec: EmbeddingSpec | None = None


class SearchChunkProjection(SearchChunk):
    """Canonical chunk plus ownership/version identity for projection metadata."""

    source_id: UUID
    account_id: UUID


class AsyncSearchChunkRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _close_read_transaction(self) -> None:
        if self.session.in_transaction():
            await self.session.commit()

    async def replace_chunks(self, source_version_id: UUID, chunks: list[SearchChunk]) -> None:
        if any(chunk.source_version_id != source_version_id for chunk in chunks):
            raise ValueError("all chunks must belong to the requested source version")
        await self._close_read_transaction()
        async with self.session.begin():
            block_ids = {block_id for chunk in chunks for block_id in chunk.block_ids}
            if block_ids:
                rows = (await self.session.execute(select(ReadingBlockRow.block_id).where(
                    ReadingBlockRow.source_version_id == source_version_id,
                    ReadingBlockRow.block_id.in_(block_ids)))).scalars().all()
                if len(rows) != len(block_ids):
                    raise ValueError("all chunk block references must belong to the source version")
            await self.session.execute(delete(SearchChunkRow).where(SearchChunkRow.source_version_id == source_version_id))
            self.session.add_all([SearchChunkRow(chunk_id=c.chunk_id, source_version_id=source_version_id,
                text=c.text, block_ids=[str(i) for i in c.block_ids], embedding_version=c.embedding_version,
                retrieval_metadata=c.metadata, embedding=c.embedding,
                embedding_spec=c.embedding_spec.model_dump(mode="json") if c.embedding_spec else None) for c in chunks])

    async def get(self, chunk_id: UUID) -> SearchChunk | None:
        row = (await self.session.execute(select(SearchChunkRow).where(SearchChunkRow.chunk_id == chunk_id))).scalar_one_or_none()
        return SearchChunk(chunk_id=row.chunk_id, source_version_id=row.source_version_id, text=row.text,
                           block_ids=[UUID(i) for i in row.block_ids], embedding_version=row.embedding_version,
                           metadata=row.retrieval_metadata, embedding=row.embedding,
                           embedding_spec=EmbeddingSpec.model_validate(row.embedding_spec) if row.embedding_spec else None) if row else None

    async def list_for_embedding(self, source_version_id: UUID) -> list[SearchChunk]:
        rows = (await self.session.execute(select(SearchChunkRow).where(
            SearchChunkRow.source_version_id == source_version_id).order_by(SearchChunkRow.chunk_id))).scalars().all()
        return [SearchChunk(chunk_id=row.chunk_id, source_version_id=row.source_version_id, text=row.text,
                            block_ids=[UUID(i) for i in row.block_ids], embedding_version=row.embedding_version,
                            metadata=row.retrieval_metadata, embedding=row.embedding,
                            embedding_spec=EmbeddingSpec.model_validate(row.embedding_spec) if row.embedding_spec else None)
                for row in rows]

    async def save_embedding(self, chunk_id: UUID, vector: list[float], specification: EmbeddingSpec) -> None:
        await self._close_read_transaction()
        async with self.session.begin():
            row = (await self.session.execute(select(SearchChunkRow).where(
                SearchChunkRow.chunk_id == chunk_id).with_for_update())).scalar_one_or_none()
            if row is None:
                raise ValueError("search chunk does not exist")
            if row.embedding is not None and (row.embedding != vector or row.embedding_spec != specification.model_dump(mode="json")):
                raise ValueError("search chunk embedding is immutable")
            row.embedding, row.embedding_spec = vector, specification.model_dump(mode="json")
            row.embedding_version = specification.version

    async def list_projection_chunks(self, source_version_id: UUID) -> list[SearchChunkProjection]:
        rows = (await self.session.execute(select(SearchChunkRow, SourceVersionRow, SourceRow)
            .join(SourceVersionRow, SourceVersionRow.source_version_id == SearchChunkRow.source_version_id)
            .join(SourceRow, SourceRow.source_id == SourceVersionRow.source_id)
            .where(SearchChunkRow.source_version_id == source_version_id)
            .order_by(SearchChunkRow.chunk_id))).all()
        return [SearchChunkProjection(chunk_id=chunk.chunk_id, source_version_id=chunk.source_version_id,
            source_id=source.source_id, account_id=source.account_id, text=chunk.text,
            block_ids=[UUID(i) for i in chunk.block_ids], embedding_version=chunk.embedding_version,
            metadata=chunk.retrieval_metadata, embedding=chunk.embedding,
            embedding_spec=EmbeddingSpec.model_validate(chunk.embedding_spec) if chunk.embedding_spec else None) for chunk, version, source in rows
            if version.status == "ready"]

    async def list_projection_chunks_for_ingestion(self, source_version_id: UUID) -> list[SearchChunkProjection]:
        """Read canonical chunks for projection before the version is READY."""
        rows = (await self.session.execute(select(SearchChunkRow, SourceVersionRow, SourceRow)
            .join(SourceVersionRow, SourceVersionRow.source_version_id == SearchChunkRow.source_version_id)
            .join(SourceRow, SourceRow.source_id == SourceVersionRow.source_id)
            .where(SearchChunkRow.source_version_id == source_version_id)
            .order_by(SearchChunkRow.chunk_id))).all()
        return [SearchChunkProjection(chunk_id=chunk.chunk_id, source_version_id=chunk.source_version_id,
            source_id=source.source_id, account_id=source.account_id, text=chunk.text,
            block_ids=[UUID(i) for i in chunk.block_ids], embedding_version=chunk.embedding_version,
            metadata=chunk.retrieval_metadata, embedding=chunk.embedding,
            embedding_spec=EmbeddingSpec.model_validate(chunk.embedding_spec) if chunk.embedding_spec else None)
                for chunk, _version, source in rows]
