"""Worker stage that embeds canonical search chunks through Gemini."""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Protocol
from uuid import UUID

from pydantic import Field

from netra_api.content.settings import ContentSettings
from netra_api.content.retrieval.chunks import SearchChunk
from netra_api.content.retrieval.embeddings import EmbeddingProvider, EmbeddingQuotaError, EmbeddingSpec
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

logger = logging.getLogger(__name__)


class EmbedTextPayload(IngestionJobPayload):
    block_ids: list[UUID] = Field(default_factory=list)


class EmbeddingChunkStore(Protocol):
    async def list_for_embedding(self, source_version_id: UUID) -> list[SearchChunk]: ...
    async def save_embedding(self, chunk_id: UUID, vector: list[float], specification: EmbeddingSpec) -> None: ...


class EmbedTextJob:
    # Implementation bounds for waiting out an exhausted provider quota, not
    # product policy. The lease heartbeat renews while a stage runs, so a wait
    # of this size does not lose the claim.
    DEFAULT_QUOTA_WAIT_ATTEMPTS = 6
    DEFAULT_QUOTA_WAIT_SECONDS = 60.0
    MAX_QUOTA_WAIT_SECONDS = 120.0

    def __init__(self, versions: IngestionVersionStore, chunks: EmbeddingChunkStore,
                 embedder: EmbeddingProvider, settings: ContentSettings | None = None,
                 scheduler: StageScheduler | None = None,
                 quota_wait_attempts: int | None = None,
                 quota_wait_seconds: float | None = None,
                 sleep: Callable[[float], Awaitable[None]] | None = None) -> None:
        self.versions, self.chunks, self.embedder = versions, chunks, embedder
        self.settings = settings or ContentSettings()
        self.scheduler = scheduler
        self.quota_wait_attempts = (
            self.DEFAULT_QUOTA_WAIT_ATTEMPTS if quota_wait_attempts is None else quota_wait_attempts)
        self.quota_wait_seconds = (
            self.DEFAULT_QUOTA_WAIT_SECONDS if quota_wait_seconds is None else quota_wait_seconds)
        self.max_quota_wait_seconds = self.MAX_QUOTA_WAIT_SECONDS
        self._sleep = sleep or asyncio.sleep

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
        # retry pays only for the remainder. That only holds if each batch is
        # persisted as it returns: embedding everything before saving anything
        # means one failure at the end discards every vector already paid for,
        # and a document larger than the provider's per-minute quota can then
        # never finish, however many times the job is retried.
        expected = EmbeddingSpec.from_settings(self.settings)
        size = max(1, self.settings.gemini_embedding_batch_size)
        for start in range(0, len(chunks), size):
            batch = chunks[start : start + size]
            results = await self._embed_with_quota_waits([chunk.text for chunk in batch])
            if len(results) != len(batch) or any(
                    result.specification != expected or len(result.vector) != expected.dimension
                    for result in results):
                # A model/dimension mismatch is configuration, not a transient fault.
                raise PermanentJobError("embedding response is incompatible with projection configuration")
            for chunk, result in zip(batch, results):
                await self.chunks.save_embedding(chunk.chunk_id, result.vector, result.specification)
        await self.versions.mark_stage_complete_internal(payload.source_version_id, "embedded")
        await self._schedule_projection(payload)

    async def _embed_with_quota_waits(self, texts: list[str]) -> list:
        """Embed one batch, waiting out an exhausted quota a bounded number of times.

        A quota refusal is temporal: the same request succeeds after the
        window resets. Waiting inside the job keeps the vectors already
        persisted and lets one attempt finish a document that is larger than
        the per-minute allowance, which the dispatcher's own backoff cannot do
        (it is shorter than the window, and attempts are capped). The waiting
        is bounded, so an allowance that never returns still ends as a normal
        retryable failure with all progress kept.
        """

        for remaining in range(self.quota_wait_attempts, -1, -1):
            try:
                return await self.embedder.embed_batch(texts)
            except EmbeddingQuotaError as exc:
                if remaining == 0:
                    raise
                delay = exc.retry_after_seconds or self.quota_wait_seconds
                logger.warning(
                    "embedding quota exhausted; waiting %.1fs before retrying this batch (%d wait(s) left)",
                    min(delay, self.max_quota_wait_seconds), remaining)
                await self._sleep(min(delay, self.max_quota_wait_seconds))
        raise AssertionError("unreachable")  # pragma: no cover

    async def _schedule_projection(self, payload: EmbedTextPayload) -> None:
        from netra_worker.jobs.search_projection.pinecone import SearchProjectionPayload

        await schedule_next(self.scheduler, "search_projection", SearchProjectionPayload(
            idempotency_key=stage_key("search_projection", payload.source_version_id),
            source_version_id=payload.source_version_id))
