"""Embedding under an exhausted provider quota.

A document larger than the provider's per-minute allowance must still finish.
The rules under test: every batch that succeeds is persisted before the next
is attempted, a quota refusal is waited out rather than discarding paid-for
vectors, and giving up keeps all progress so the next attempt pays only for
the remainder.
"""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from netra_api.content.settings import ContentSettings
from netra_api.content.retrieval.chunks import SearchChunk
from netra_api.content.retrieval.embeddings import (
    EmbeddingProviderError, EmbeddingQuotaError, EmbeddingResult, EmbeddingSpec, _NOT_QUOTA, _quota_retry_after)
from netra_api.content.sources.models import SourceVersion, SourceVersionIngestionState, SourceVersionStatus
from netra_worker.jobs.ingestion.embed_text import EmbedTextJob, EmbedTextPayload

SETTINGS = ContentSettings(gemini_embedding_dimension=3, gemini_embedding_batch_size=2)
SPEC = EmbeddingSpec.from_settings(SETTINGS)


def _version():
    return SourceVersion(
        source_version_id=uuid4(), source_id=uuid4(), version_number=1,
        status=SourceVersionStatus.PENDING, created_at=datetime.now(timezone.utc), object_key="source.pdf",
        content_hash="hash", parser_name="pymupdf", parser_version="1.28.2", parser_config={},
        ingestion_state=SourceVersionIngestionState.BLOCKS_BUILT)


def _chunks(version, count):
    return [SearchChunk(source_version_id=version.source_version_id, text=f"chunk {i}",
                        block_ids=[], embedding_version="pending") for i in range(count)]


class Versions:
    def __init__(self, version):
        self.version = version

    async def get_version_internal(self, _id):
        return self.version

    async def mark_stage_complete_internal(self, _id, stage):
        self.version.completed_stages.append(stage)

    async def mark_ready_internal(self, _id):
        return self.version

    async def active_version_number_internal(self, _source_id):
        return None


class Chunks:
    def __init__(self, chunks):
        self.chunks = chunks
        self.saved: dict = {}

    async def list_for_embedding(self, _id):
        return list(self.chunks)

    async def save_embedding(self, chunk_id, vector, specification):
        self.saved[chunk_id] = (vector, specification)
        for chunk in self.chunks:
            if chunk.chunk_id == chunk_id:
                chunk.embedding, chunk.embedding_spec = vector, specification


class Embedder:
    """Fails every call after ``allowance`` texts, like a per-minute quota."""

    def __init__(self, allowance: int, resets: bool = True):
        self.allowance, self.resets = allowance, resets
        self.spent = 0
        self.calls = 0

    async def embed_batch(self, texts):
        self.calls += 1
        if self.spent + len(texts) > self.allowance:
            raise EmbeddingQuotaError(retry_after_seconds=37.0)
        self.spent += len(texts)
        return [EmbeddingResult(vector=[0.1, 0.2, 0.3], specification=SPEC) for _ in texts]

    async def reset(self, _seconds):
        if self.resets:
            self.spent = 0


def _payload(version):
    return EmbedTextPayload(idempotency_key="k", source_id=version.source_id,
                            source_version_id=version.source_version_id)


@pytest.mark.asyncio
async def test_a_document_larger_than_the_quota_finishes_in_one_attempt():
    version = _version()
    chunks = Chunks(_chunks(version, 6))
    embedder = Embedder(allowance=2)          # two texts per window, six to embed
    waits: list[float] = []

    async def sleep(seconds):
        waits.append(seconds)
        await embedder.reset(seconds)

    await EmbedTextJob(Versions(version), chunks, embedder, SETTINGS, sleep=sleep).handle(_payload(version))

    assert len(chunks.saved) == 6
    assert "embedded" in version.completed_stages
    assert waits == [37.0, 37.0]              # the provider's own delay, once per exhausted window


@pytest.mark.asyncio
async def test_each_batch_is_saved_before_the_next_is_attempted():
    version = _version()
    chunks = Chunks(_chunks(version, 6))
    embedder = Embedder(allowance=4, resets=False)   # never recovers after four
    saved_when_exhausted: list[int] = []

    async def sleep(_seconds):
        saved_when_exhausted.append(len(chunks.saved))

    with pytest.raises(EmbeddingQuotaError):
        await EmbedTextJob(Versions(version), chunks, embedder, SETTINGS,
                           quota_wait_attempts=1, sleep=sleep).handle(_payload(version))

    # The four vectors already paid for are persisted, not discarded.
    assert len(chunks.saved) == 4
    assert saved_when_exhausted == [4]
    assert "embedded" not in version.completed_stages


@pytest.mark.asyncio
async def test_the_next_attempt_pays_only_for_the_remainder():
    version = _version()
    chunks = Chunks(_chunks(version, 6))
    first = Embedder(allowance=4, resets=False)

    with pytest.raises(EmbeddingQuotaError):
        await EmbedTextJob(Versions(version), chunks, first, SETTINGS,
                           quota_wait_attempts=0).handle(_payload(version))
    assert len(chunks.saved) == 4

    second = Embedder(allowance=100)
    await EmbedTextJob(Versions(version), chunks, second, SETTINGS).handle(_payload(version))

    assert len(chunks.saved) == 6
    assert second.spent == 2          # only the remaining two were re-embedded
    assert "embedded" in version.completed_stages


@pytest.mark.asyncio
async def test_waiting_is_bounded_and_ends_as_a_retryable_failure():
    version = _version()
    chunks = Chunks(_chunks(version, 2))
    embedder = Embedder(allowance=0, resets=False)
    waits: list[float] = []

    with pytest.raises(EmbeddingQuotaError):
        await EmbedTextJob(Versions(version), chunks, embedder, SETTINGS, quota_wait_attempts=3,
                           sleep=lambda s: waits.append(s) or _done()).handle(_payload(version))

    assert len(waits) == 3            # bounded: it does not wait forever
    assert chunks.saved == {}
    assert "embedded" not in version.completed_stages


@pytest.mark.asyncio
async def test_a_quota_refusal_without_a_stated_delay_uses_the_configured_wait():
    version = _version()
    chunks = Chunks(_chunks(version, 2))
    waits: list[float] = []

    class Silent:
        def __init__(self):
            self.calls = 0

        async def embed_batch(self, texts):
            self.calls += 1
            if self.calls == 1:
                raise EmbeddingQuotaError()          # no retry_after_seconds
            return [EmbeddingResult(vector=[0.1, 0.2, 0.3], specification=SPEC) for _ in texts]

    await EmbedTextJob(Versions(version), chunks, Silent(), SETTINGS, quota_wait_seconds=45.0,
                       sleep=lambda s: waits.append(s) or _done()).handle(_payload(version))

    assert waits == [45.0]
    assert len(chunks.saved) == 2


@pytest.mark.asyncio
async def test_a_non_quota_provider_failure_is_not_waited_out():
    version = _version()
    chunks = Chunks(_chunks(version, 2))
    waits: list[float] = []

    class Broken:
        async def embed_batch(self, _texts):
            raise EmbeddingProviderError("Gemini embedding request failed")

    with pytest.raises(EmbeddingProviderError):
        await EmbedTextJob(Versions(version), chunks, Broken(), SETTINGS,
                           sleep=lambda s: waits.append(s) or _done()).handle(_payload(version))

    assert waits == []                # only an exhausted quota is worth waiting for
    assert chunks.saved == {}


def test_only_a_429_is_read_as_a_quota_failure():
    class Client429(Exception):
        code = 429

    assert _quota_retry_after(Client429("429 RESOURCE_EXHAUSTED {'retryDelay': '37.8s'}")) == 37.8
    assert _quota_retry_after(Client429("429 RESOURCE_EXHAUSTED, no delay stated")) is None
    assert _quota_retry_after(ValueError("400 INVALID_ARGUMENT")) is _NOT_QUOTA
    assert _quota_retry_after(RuntimeError("connection reset")) is _NOT_QUOTA


async def _done():
    return None
