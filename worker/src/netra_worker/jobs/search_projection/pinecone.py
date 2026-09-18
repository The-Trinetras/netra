"""Worker stage that projects persisted embeddings to Pinecone."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from netra_api.content.settings import ContentSettings
from netra_api.content.projection.pinecone import PineconeProjectionService
from netra_api.content.retrieval.chunks import SearchChunkProjection
from netra_api.content.providers.pinecone import VectorIndexProvider
from netra_api.content.sources.models import SourceVersionIngestionState

from netra_worker.runtime.job_repository import JobPayload
from netra_worker.jobs.ingestion.base import IngestionVersionStore, StageScheduler, schedule_next, stage_key
from netra_worker.runtime.errors import PermanentJobError
from netra_api.content.telemetry import instrument_stage


class SearchProjectionPayload(JobPayload):
    source_version_id: UUID


class ProjectionChunkReader(Protocol):
    async def list_projection_chunks_for_ingestion(self, source_version_id: UUID) -> list[SearchChunkProjection]: ...


class _StoredEmbeddingProvider:
    async def embed_batch(self, texts):
        raise RuntimeError("canonical embeddings are required before projection")


class _IngestionProjectionReader:
    def __init__(self, chunks: ProjectionChunkReader) -> None:
        self.chunks = chunks

    async def list_projection_chunks(self, source_version_id: UUID) -> list[SearchChunkProjection]:
        return await self.chunks.list_projection_chunks_for_ingestion(source_version_id)


class SearchProjectionJob:
    def __init__(self, versions: IngestionVersionStore, chunks: ProjectionChunkReader,
                 index: VectorIndexProvider, settings: ContentSettings | None = None,
                 scheduler: StageScheduler | None = None) -> None:
        self.versions, self.chunks, self.index = versions, chunks, index
        self.settings = settings or ContentSettings()
        self.scheduler = scheduler

    @instrument_stage("project_vectors")
    async def handle(self, payload: SearchProjectionPayload) -> None:
        version = await self.versions.get_version_internal(payload.source_version_id)
        if version.ingestion_state not in {
                SourceVersionIngestionState.EMBEDDED,
                SourceVersionIngestionState.PROJECTED,
                SourceVersionIngestionState.READY,
                SourceVersionIngestionState.ACTIVE,
        }:
            raise PermanentJobError("source version is not ready for projection")
        service = PineconeProjectionService(
            _IngestionProjectionReader(self.chunks), _StoredEmbeddingProvider(), self.index, self.settings
        )
        await service.project_source_version(payload.source_version_id)
        await self.versions.mark_stage_complete_internal(payload.source_version_id, "projected")
        ready = await self.versions.mark_ready_internal(payload.source_version_id)
        await self._schedule_activation(ready)

    async def _schedule_activation(self, version) -> None:
        if self.scheduler is None or version is None or version.is_active:
            return
        from netra_worker.jobs.ingestion.activate_version import ActivateVersionPayload

        # Expect the currently active version so activation stays a
        # compare-and-set; pinned sessions keep their own version either way.
        active = await self.versions.active_version_number_internal(version.source_id)
        await schedule_next(self.scheduler, "activate_version", ActivateVersionPayload(
            idempotency_key=stage_key("activate_version", version.source_version_id),
            source_id=version.source_id, source_version_id=version.source_version_id,
            expected_version_number=active))
