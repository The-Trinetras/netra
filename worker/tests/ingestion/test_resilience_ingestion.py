"""Deterministic provider-failure and replay tests for ingestion stages."""

from datetime import datetime, timezone
from uuid import uuid4
import hashlib

import pytest

from netra_api.content.settings import ContentSettings
from netra_api.content.retrieval.chunks import SearchChunk, SearchChunkProjection
from netra_api.content.retrieval.embeddings import EmbeddingSpec
from netra_api.content.sources.models import SourceVersion, SourceVersionIngestionState, SourceVersionStatus
from netra_worker.jobs.ingestion.embed_text import EmbedTextJob, EmbedTextPayload
from netra_worker.jobs.ingestion.parse_document import ParseDocumentJob, ParseDocumentPayload
from netra_worker.jobs.search_projection.pinecone import SearchProjectionJob, SearchProjectionPayload


def _version(state=SourceVersionIngestionState.PENDING):
    source_id = uuid4()
    return SourceVersion(source_version_id=uuid4(), source_id=source_id, version_number=1,
        status=SourceVersionStatus.PENDING, created_at=datetime.now(timezone.utc), object_key="source.pdf",
        content_hash="hash", parser_name="pymupdf", parser_version="1.28.2", parser_config={},
        ingestion_state=state)


class Versions:
    def __init__(self, version): self.version = version
    async def get_version_internal(self, version_id):
        assert version_id == self.version.source_version_id
        return self.version
    async def mark_stage_complete_internal(self, _version_id, stage): self.version.completed_stages.append(stage)
    async def mark_failed_internal(self, _version_id):
        self.version.ingestion_state = SourceVersionIngestionState.FAILED
        self.version.status = SourceVersionStatus.FAILED
    async def mark_ready_internal(self, _version_id):
        self.version.ingestion_state = SourceVersionIngestionState.READY
        self.version.status = SourceVersionStatus.READY


def _payload(version, **values):
    return {"source_id": version.source_id, "source_version_id": version.source_version_id,
            "idempotency_key": str(uuid4()), **values}


@pytest.mark.asyncio
async def test_parser_failure_marks_version_failed_and_writes_no_parsed_output():
    version = _version(); data = b"pdf"; version.content_hash = hashlib.sha256(data).hexdigest()
    class Storage:
        async def get_object(self, _): return data
    class Parser:
        def parse_bytes(self, _): raise ValueError("malformed PDF")
    class Parsed:
        async def put(self, *_): raise AssertionError("invalid output must not be written")
    with pytest.raises(ValueError, match="malformed"):
        await ParseDocumentJob(Versions(version), Storage(), Parsed(), Parser()).handle(
            ParseDocumentPayload(**_payload(version, object_key="source.pdf", content_type="application/pdf")))
    assert version.ingestion_state == SourceVersionIngestionState.FAILED
    assert version.completed_stages == []


@pytest.mark.asyncio
async def test_embedding_provider_failure_keeps_canonical_chunks_and_stage_incomplete():
    version = _version(SourceVersionIngestionState.BLOCKS_BUILT)
    chunk = SearchChunk(source_version_id=version.source_version_id, text="canonical", block_ids=[], embedding_version="pending")
    class Chunks:
        async def list_for_embedding(self, _): return [chunk]
        async def save_embedding(self, *_): raise AssertionError("provider failed before persistence")
    class Embedder:
        async def embed_batch(self, _): raise RuntimeError("Gemini unavailable")
    with pytest.raises(RuntimeError, match="Gemini unavailable"):
        await EmbedTextJob(Versions(version), Chunks(), Embedder(), ContentSettings(gemini_embedding_dimension=3)).handle(
            EmbedTextPayload(**_payload(version)))
    assert chunk.embedding is None
    assert version.ingestion_state == SourceVersionIngestionState.BLOCKS_BUILT


@pytest.mark.asyncio
async def test_projection_failure_does_not_mark_ready_and_replay_uses_same_vector_id():
    version = _version(SourceVersionIngestionState.EMBEDDED)
    spec = EmbeddingSpec(model="gemini-embedding-001", dimension=3, version="gemini-embedding-001:3")
    chunk = SearchChunkProjection(chunk_id=uuid4(), source_version_id=version.source_version_id,
        source_id=version.source_id, account_id=uuid4(), text="canonical", block_ids=[],
        embedding_version=spec.version, embedding=[0.1, 0.2, 0.3], embedding_spec=spec)
    class Chunks:
        async def list_projection_chunks_for_ingestion(self, _): return [chunk]
    class Index:
        def __init__(self): self.ids = []; self.fail = True
        async def upsert(self, _namespace, vectors):
            if self.fail: raise RuntimeError("Pinecone unavailable")
            self.ids.extend(item[0] for item in vectors)
    index = Index(); job = SearchProjectionJob(Versions(version), Chunks(), index, ContentSettings(gemini_embedding_dimension=3))
    payload = SearchProjectionPayload(**_payload(version))
    with pytest.raises(RuntimeError, match="Pinecone unavailable"): await job.handle(payload)
    assert version.ingestion_state == SourceVersionIngestionState.EMBEDDED
    index.fail = False; await job.handle(payload)
    assert index.ids == [str(chunk.chunk_id)]
