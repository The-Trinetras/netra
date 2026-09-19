from types import SimpleNamespace
from uuid import uuid4

import pymupdf
import pytest

from netra_api.content.settings import ContentSettings
from netra_api.content.providers.llamaparse import ParsedBlock
from netra_api.content.providers.pymupdf import PdfParseResult, PdfParseStatus
from netra_api.content.retrieval.chunks import SearchChunk, SearchChunkProjection
from netra_api.content.retrieval.embeddings import EmbeddingResult, EmbeddingSpec
from netra_api.content.sources.models import SourceVersion, SourceVersionIngestionState, SourceVersionStatus
from netra_api.content.providers.pinecone import VectorMatch
from netra_api.content.reading.blocks import ReadingBlock
from netra_worker.jobs.ingestion.base import IngestionJobPayload
from netra_worker.jobs.ingestion.parse_document import IngestionError, ParseDocumentJob, ParseDocumentPayload
from netra_worker.jobs.ingestion.build_blocks import BuildBlocksJob, BuildBlocksPayload
from netra_worker.jobs.ingestion.embed_text import EmbedTextJob, EmbedTextPayload
from netra_worker.jobs.search_projection.pinecone import SearchProjectionJob, SearchProjectionPayload


def _pdf(text="Binary search divides a sorted search space in half."):
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    data = document.tobytes()
    document.close()
    return data


def _version(source_id, data):
    import hashlib
    return SourceVersion(source_version_id=uuid4(), source_id=source_id, version_number=1,
        status=SourceVersionStatus.PENDING, created_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        object_key="source.pdf", content_hash=hashlib.sha256(data).hexdigest(), parser_name="pymupdf",
        parser_version="1.28.2", parser_config={"sort": True})


class Versions:
    def __init__(self, version):
        self.version = version
        completed_by_state = {
            SourceVersionIngestionState.EMBEDDED: ["parsing", "blocks_built", "embedded"],
            SourceVersionIngestionState.PROJECTED: ["parsing", "blocks_built", "embedded", "projected"],
            SourceVersionIngestionState.READY: ["parsing", "blocks_built", "embedded", "projected"],
            SourceVersionIngestionState.ACTIVE: ["parsing", "blocks_built", "embedded", "projected"],
        }
        if not version.completed_stages:
            version.completed_stages = completed_by_state.get(version.ingestion_state, [])
    async def get_version_internal(self, source_version_id):
        if source_version_id != self.version.source_version_id: raise RuntimeError("wrong version")
        return self.version
    async def mark_stage_complete_internal(self, source_version_id, stage):
        assert source_version_id == self.version.source_version_id
        self.version.completed_stages.append(stage)
        self.version.ingestion_state = {"parsing": SourceVersionIngestionState.PARSING,
            "blocks_built": SourceVersionIngestionState.BLOCKS_BUILT,
            "embedded": SourceVersionIngestionState.EMBEDDED,
            "projected": SourceVersionIngestionState.PROJECTED}[stage]
        self.version.status = SourceVersionStatus.PROCESSING
    async def mark_failed_internal(self, source_version_id):
        assert source_version_id == self.version.source_version_id
        self.version.ingestion_state = SourceVersionIngestionState.FAILED
        self.version.status = SourceVersionStatus.FAILED

    async def mark_ready_internal(self, source_version_id):
        assert source_version_id == self.version.source_version_id
        assert set(self.version.completed_stages) == {"parsing", "blocks_built", "embedded", "projected"}
        self.version.ingestion_state = SourceVersionIngestionState.READY
        self.version.status = SourceVersionStatus.READY


class Storage:
    def __init__(self, data): self.data = data
    async def get_object(self, key): return self.data[key]


class ParsedStore:
    def __init__(self): self.values = {}
    async def put(self, key, blocks, content_hash): self.values[key] = (content_hash, blocks)
    async def get(self, key): return self.values[key]


def _payload(version, **values):
    return values | {"source_id": version.source_id, "source_version_id": version.source_version_id,
                     "idempotency_key": str(uuid4())}


@pytest.mark.asyncio
async def test_parse_validates_hash_and_persists_page_aware_output():
    data = _pdf()
    version = _version(uuid4(), data)
    version.ingestion_state = SourceVersionIngestionState.PARSING
    versions, parsed = Versions(version), ParsedStore()
    await ParseDocumentJob(versions, Storage({"source.pdf": data}), parsed).handle(
        ParseDocumentPayload(**_payload(version, object_key="source.pdf", content_type="application/pdf")))
    assert version.ingestion_state == SourceVersionIngestionState.PARSING
    assert parsed.values
    assert parsed.values[next(iter(parsed.values))][1][0].page_index == 0


@pytest.mark.asyncio
async def test_parse_hash_mismatch_and_malformed_pdf_fail_closed():
    data = _pdf()
    version = _version(uuid4(), data)
    versions = Versions(version)
    job = ParseDocumentJob(versions, Storage({"source.pdf": b"wrong"}), ParsedStore())
    with pytest.raises(IngestionError, match="hash mismatch"):
        await job.handle(ParseDocumentPayload(**_payload(version, object_key="source.pdf", content_type="application/pdf")))
    assert version.ingestion_state == SourceVersionIngestionState.FAILED

    version = _version(uuid4(), b"bad")
    with pytest.raises(Exception):
        await ParseDocumentJob(Versions(version), Storage({"source.pdf": b"bad"}), ParsedStore()).handle(
            ParseDocumentPayload(**_payload(version, object_key="source.pdf", content_type="application/pdf")))


@pytest.mark.asyncio
async def test_parse_ocr_required_fails_without_fabricating_text():
    data = _pdf()
    version = _version(uuid4(), data)

    class OcrRequiredParser:
        def parse_bytes(self, _data):
            return PdfParseResult(status=PdfParseStatus.OCR_REQUIRED, requires_ocr=True)

    parsed = ParsedStore()
    with pytest.raises(IngestionError, match="ocr_required"):
        await ParseDocumentJob(Versions(version), Storage({"source.pdf": data}), parsed,
                               OcrRequiredParser()).handle(
            ParseDocumentPayload(**_payload(version, object_key="source.pdf", content_type="application/pdf")))
    assert not parsed.values
    assert version.ingestion_state == SourceVersionIngestionState.FAILED


@pytest.mark.asyncio
async def test_job_version_identity_prevents_cross_version_mutation():
    data = _pdf()
    first, second = _version(uuid4(), data), _version(uuid4(), data)
    versions, parsed = Versions(first), ParsedStore()
    payload = ParseDocumentPayload(**_payload(first, object_key="source.pdf", content_type="application/pdf"))
    payload.source_id = second.source_id
    with pytest.raises(IngestionError, match="identity"):
        await ParseDocumentJob(versions, Storage({"source.pdf": data}), parsed).handle(payload)
    assert not parsed.values
    assert first.ingestion_state == SourceVersionIngestionState.PENDING


@pytest.mark.asyncio
async def test_build_blocks_replaces_canonical_records_idempotently_and_preserves_provenance():
    data = _pdf()
    version = _version(uuid4(), data)
    version.ingestion_state = SourceVersionIngestionState.PARSING
    versions, parsed = Versions(version), ParsedStore()
    parsed.blocks = [ParsedBlock(text="A paragraph.", block_type_hint="paragraph", page_index=2,
                                 structured_location={"page_index": 2, "parent_id": "section-1"}, parent_id="section-1")]
    await parsed.put("parsed", parsed.blocks, version.content_hash)
    class Blocks:
        def __init__(self): self.values = []
        async def replace_blocks(self, source_version_id, blocks): self.values = blocks
    class Chunks:
        def __init__(self): self.values = []
        async def replace_chunks(self, source_version_id, chunks): self.values = chunks
    block_store, chunk_store = Blocks(), Chunks()
    job = BuildBlocksJob(versions, parsed, block_store, chunk_store)
    payload = BuildBlocksPayload(**_payload(version, parsed_object_key="parsed"))
    await job.handle(payload)
    await job.handle(payload)
    assert len(block_store.values) == 1
    assert len(chunk_store.values) == 1
    assert block_store.values[0].page_index == 2
    assert chunk_store.values[0].block_ids == [block_store.values[0].block_id]
    assert version.ingestion_state == SourceVersionIngestionState.BLOCKS_BUILT


class Chunks:
    def __init__(self, chunks): self.values = chunks; self.saved = []
    async def list_for_embedding(self, source_version_id): return self.values
    async def save_embedding(self, chunk_id, vector, specification):
        self.saved.append((chunk_id, vector, specification))
        for chunk in self.values:
            if chunk.chunk_id == chunk_id:
                chunk.embedding, chunk.embedding_spec, chunk.embedding_version = vector, specification, specification.version


class Embedder:
    def __init__(self, dimension=3, batch_size=None): self.dimension, self.calls = dimension, []
    async def embed_batch(self, texts):
        self.calls.append(list(texts))
        spec = EmbeddingSpec(model="gemini-embedding-001", dimension=self.dimension, version=f"gemini-embedding-001:{self.dimension}")
        return [EmbeddingResult(vector=[0.1] * self.dimension, specification=spec) for _ in texts]


def _chunk(version):
    return SearchChunk(source_version_id=version.source_version_id, text="chunk", block_ids=[], embedding_version="pending")


@pytest.mark.asyncio
async def test_embedding_batches_and_marks_stage_only_after_valid_results():
    data = _pdf(); version = _version(uuid4(), data)
    version.ingestion_state = SourceVersionIngestionState.BLOCKS_BUILT
    chunks = Chunks([_chunk(version), _chunk(version)])
    embedder = Embedder(3)
    await EmbedTextJob(Versions(version), chunks, embedder, ContentSettings(gemini_embedding_dimension=3)).handle(
        EmbedTextPayload(**_payload(version)))
    assert len(embedder.calls) == 1
    assert len(chunks.saved) == 2
    assert version.ingestion_state == SourceVersionIngestionState.EMBEDDED


@pytest.mark.asyncio
async def test_embedding_dimension_failure_does_not_mark_embedded():
    from netra_worker.runtime.errors import PermanentJobError

    data = _pdf(); version = _version(uuid4(), data)
    version.ingestion_state = SourceVersionIngestionState.BLOCKS_BUILT
    # A configuration mismatch is permanent: retrying cannot change the model.
    with pytest.raises(PermanentJobError, match="incompatible"):
        await EmbedTextJob(Versions(version), Chunks([_chunk(version)]), Embedder(2), ContentSettings(gemini_embedding_dimension=3)).handle(
            EmbedTextPayload(**_payload(version)))
    assert version.ingestion_state == SourceVersionIngestionState.BLOCKS_BUILT


class ProjectionChunks:
    def __init__(self, version, chunks): self.version, self.chunks = version, chunks
    async def list_projection_chunks_for_ingestion(self, source_version_id): return self.chunks


class Index:
    def __init__(self, error=False): self.calls, self.error = [], error
    async def upsert(self, namespace, vectors):
        if self.error: raise RuntimeError("pinecone unavailable")
        self.calls.append((namespace, vectors))
    async def query(self, *args, **kwargs): return []
    async def delete(self, *args, **kwargs): pass


@pytest.mark.asyncio
async def test_projection_uses_persisted_embedding_and_marks_projected_only_after_success():
    data = _pdf(); version = _version(uuid4(), data)
    version.ingestion_state = SourceVersionIngestionState.EMBEDDED
    spec = EmbeddingSpec(model="gemini-embedding-001", dimension=3, version="gemini-embedding-001:3")
    chunk = SearchChunkProjection(chunk_id=uuid4(), source_version_id=version.source_version_id, source_id=version.source_id,
        account_id=uuid4(), text="chunk", block_ids=[], embedding_version=spec.version, embedding=[0.1, 0.2, 0.3], embedding_spec=spec)
    index = Index()
    await SearchProjectionJob(Versions(version), ProjectionChunks(version, [chunk]), index,
                              ContentSettings(gemini_embedding_dimension=3)).handle(
        SearchProjectionPayload(idempotency_key=str(uuid4()), source_version_id=version.source_version_id))
    assert len(index.calls) == 1
    assert index.calls[0][1][0][0] == str(chunk.chunk_id)
    assert version.ingestion_state == SourceVersionIngestionState.READY
    assert version.status == SourceVersionStatus.READY


@pytest.mark.asyncio
async def test_projection_replay_reuses_deterministic_vector_id():
    data = _pdf(); version = _version(uuid4(), data)
    version.ingestion_state = SourceVersionIngestionState.EMBEDDED
    spec = EmbeddingSpec(model="gemini-embedding-001", dimension=3, version="gemini-embedding-001:3")
    chunk = SearchChunkProjection(chunk_id=uuid4(), source_version_id=version.source_version_id,
        source_id=version.source_id, account_id=uuid4(), text="chunk", block_ids=[],
        embedding_version=spec.version, embedding=[0.1, 0.2, 0.3], embedding_spec=spec)
    index = Index()
    job = SearchProjectionJob(Versions(version), ProjectionChunks(version, [chunk]), index,
                              ContentSettings(gemini_embedding_dimension=3))
    payload = SearchProjectionPayload(idempotency_key=str(uuid4()), source_version_id=version.source_version_id)
    await job.handle(payload)
    await job.handle(payload)
    assert [call[1][0][0] for call in index.calls] == [str(chunk.chunk_id), str(chunk.chunk_id)]


@pytest.mark.asyncio
async def test_projection_failure_preserves_canonical_chunks_and_stage():
    data = _pdf(); version = _version(uuid4(), data)
    version.ingestion_state = SourceVersionIngestionState.EMBEDDED
    spec = EmbeddingSpec(model="gemini-embedding-001", dimension=3, version="gemini-embedding-001:3")
    chunk = SearchChunkProjection(chunk_id=uuid4(), source_version_id=version.source_version_id, source_id=version.source_id,
        account_id=uuid4(), text="canonical", block_ids=[], embedding_version=spec.version, embedding=[0.1] * 3, embedding_spec=spec)
    with pytest.raises(Exception, match="pinecone unavailable"):
        await SearchProjectionJob(Versions(version), ProjectionChunks(version, [chunk]), Index(True),
                                  ContentSettings(gemini_embedding_dimension=3)).handle(
            SearchProjectionPayload(idempotency_key=str(uuid4()), source_version_id=version.source_version_id))
    assert chunk.text == "canonical"
    assert version.ingestion_state == SourceVersionIngestionState.EMBEDDED


@pytest.mark.asyncio
async def test_ingestion_pipeline_runs_from_pdf_to_projection():
    data = _pdf("A complete ingestion path.")
    version = _version(uuid4(), data)
    versions, parsed = Versions(version), ParsedStore()
    storage = Storage({"source.pdf": data})

    await ParseDocumentJob(versions, storage, parsed).handle(
        ParseDocumentPayload(**_payload(version, object_key="source.pdf",
                                         content_type="application/pdf",
                                         parsed_object_key="parsed")))

    block_store, chunk_store = type("Blocks", (), {"values": [],
        "replace_blocks": lambda self, _version_id, blocks: _async_set(self, "values", blocks)})(), type(
        "Chunks", (), {"values": [],
        "replace_chunks": lambda self, _version_id, chunks: _async_set(self, "values", chunks)})()

    async def _parsed(key):
        return parsed.values[key]

    parsed.get = _parsed
    await BuildBlocksJob(versions, parsed, block_store, chunk_store).handle(
        BuildBlocksPayload(**_payload(version, parsed_object_key="parsed")))
    assert version.ingestion_state == SourceVersionIngestionState.BLOCKS_BUILT

    embedding_chunks = Chunks(chunk_store.values)
    await EmbedTextJob(versions, embedding_chunks, Embedder(3),
                       ContentSettings(gemini_embedding_dimension=3)).handle(
        EmbedTextPayload(**_payload(version)))
    assert version.ingestion_state == SourceVersionIngestionState.EMBEDDED

    account_id = uuid4()
    projections = [SearchChunkProjection(
        chunk_id=chunk.chunk_id, source_version_id=version.source_version_id,
        source_id=version.source_id, account_id=account_id, text=chunk.text,
        block_ids=chunk.block_ids, embedding_version=chunk.embedding_version,
        embedding=chunk.embedding, embedding_spec=chunk.embedding_spec,
        metadata=chunk.metadata,
    ) for chunk in embedding_chunks.values]
    index = Index()
    await SearchProjectionJob(
        versions, ProjectionChunks(version, projections), index,
        ContentSettings(gemini_embedding_dimension=3),
    ).handle(SearchProjectionPayload(
        idempotency_key=str(uuid4()), source_version_id=version.source_version_id,
    ))
    assert version.ingestion_state == SourceVersionIngestionState.READY
    assert len(index.calls) == 1


async def _async_set(instance, attribute, value):
    setattr(instance, attribute, value)


class _NoWrites:
    async def replace_blocks(self, *_):
        raise AssertionError("a replayed stage must not rewrite blocks")

    async def replace_chunks(self, *_):
        raise AssertionError("a replayed stage must not replace embedded chunks")


class _NoReads:
    async def get(self, _key):
        raise AssertionError("a replayed stage must not re-read parsed output")


@pytest.mark.asyncio
async def test_build_blocks_replay_after_embedding_is_a_noop():
    version = _version(uuid4(), _pdf())
    version.ingestion_state = SourceVersionIngestionState.EMBEDDED
    versions = Versions(version)
    await BuildBlocksJob(versions, _NoReads(), _NoWrites(), _NoWrites()).handle(
        BuildBlocksPayload(**_payload(version, parsed_object_key="parsed.json")))
    assert version.ingestion_state == SourceVersionIngestionState.EMBEDDED


@pytest.mark.asyncio
async def test_build_blocks_for_another_source_is_permanent():
    from netra_worker.runtime.errors import PermanentJobError

    version = _version(uuid4(), _pdf())
    version.ingestion_state = SourceVersionIngestionState.PARSING
    payload = _payload(version, parsed_object_key="parsed.json") | {"source_id": uuid4()}
    with pytest.raises(PermanentJobError):
        await BuildBlocksJob(Versions(version), _NoReads(), _NoWrites(), _NoWrites()).handle(
            BuildBlocksPayload(**payload))
