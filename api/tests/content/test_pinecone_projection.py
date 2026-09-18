from types import SimpleNamespace
from uuid import uuid4

import pytest

from netra_api.content.settings import ContentSettings
from netra_api.content.providers.pinecone import PineconeVectorIndex, VectorIndexProviderError
from netra_api.content.projection.pinecone import PineconeProjectionService
from netra_api.content.retrieval.chunks import SearchChunkProjection
from netra_api.content.retrieval.embeddings import EmbeddingResult, EmbeddingSpec


class _FakeIndex:
    def __init__(self):
        self.upserts, self.deletes, self.queries = [], [], []

    def upsert(self, **kwargs): self.upserts.append(kwargs)
    def delete(self, **kwargs): self.deletes.append(kwargs)
    def query(self, **kwargs):
        self.queries.append(kwargs)
        return {"matches": [{"id": "b", "score": .5, "metadata": {"x": 1}},
                             {"id": "a", "score": .5, "metadata": {"x": 2}}]}


class _FakeClient:
    def __init__(self, index): self.index = index
    def Index(self, name): return self.index


def _settings(**values):
    return ContentSettings(pinecone_api_key="test-key", pinecone_index_name="test-index", **values)


@pytest.mark.asyncio
async def test_pinecone_query_parses_and_deterministically_orders_matches():
    index = _FakeIndex()
    provider = PineconeVectorIndex(_settings(), _FakeClient(index))
    matches = await provider.query("netra", [0.1, 0.2], 2, {"account_id": "account"})
    assert [match.id for match in matches] == ["a", "b"]
    assert index.queries[0]["filter"] == {"account_id": "account"}


@pytest.mark.asyncio
async def test_pinecone_upsert_preserves_ids_metadata_and_is_repeatable():
    index = _FakeIndex()
    provider = PineconeVectorIndex(_settings(pinecone_upsert_batch_size=1), _FakeClient(index))
    vectors = [("chunk-1", [1.0, 2.0], {"account_id": "account", "source_version_id": "version"})]
    await provider.upsert("netra", vectors)
    await provider.upsert("netra", vectors)
    assert index.upserts[0]["vectors"][0]["id"] == "chunk-1"
    assert index.upserts[0]["vectors"][0]["metadata"]["account_id"] == "account"
    assert len(index.upserts) == 2


@pytest.mark.asyncio
async def test_pinecone_provider_translates_provider_failure():
    class BrokenIndex(_FakeIndex):
        def upsert(self, **kwargs): raise RuntimeError("provider details")
    provider = PineconeVectorIndex(_settings(), _FakeClient(BrokenIndex()))
    with pytest.raises(VectorIndexProviderError, match="upsert failed") as error:
        await provider.upsert("netra", [("id", [1.0], {})])
    assert "provider details" not in str(error.value)


def _chunk():
    return SearchChunkProjection(chunk_id=uuid4(), source_version_id=uuid4(), source_id=uuid4(),
        account_id=uuid4(), text="canonical chunk", block_ids=[], embedding_version="old", metadata={})


class _Chunks:
    def __init__(self, chunk): self.chunk = chunk
    async def list_projection_chunks(self, source_version_id): return [self.chunk]


class _Embeddings:
    def __init__(self, result): self.result, self.texts = result, []
    async def embed_batch(self, texts): self.texts.append(list(texts)); return [self.result for _ in texts]


class _Vectors:
    def __init__(self): self.calls = []
    async def upsert(self, namespace, vectors): self.calls.append((namespace, vectors))


@pytest.mark.asyncio
async def test_projection_uses_canonical_chunk_id_and_required_metadata():
    chunk = _chunk()
    settings = _settings(gemini_embedding_dimension=3)
    spec = EmbeddingSpec.from_settings(settings)
    vectors = _Vectors()
    service = PineconeProjectionService(_Chunks(chunk), _Embeddings(EmbeddingResult(vector=[1, 2, 3], specification=spec)), vectors, settings)
    assert await service.project_source_version(chunk.source_version_id) == 1
    vector_id, values, metadata = vectors.calls[0][1][0]
    assert vector_id == str(chunk.chunk_id)
    assert values == [1, 2, 3]
    assert metadata == {"account_id": str(chunk.account_id), "source_id": str(chunk.source_id),
        "source_version_id": str(chunk.source_version_id), "chunk_id": str(chunk.chunk_id),
        "embedding_model": spec.model, "embedding_dimension": 3, "embedding_spec": spec.version}


@pytest.mark.asyncio
async def test_projection_rejects_incompatible_embedding_dimension_before_upsert():
    chunk = _chunk()
    settings = _settings(gemini_embedding_dimension=3)
    bad_spec = EmbeddingSpec(model=settings.gemini_embedding_model, dimension=2, version="wrong:2")
    vectors = _Vectors()
    service = PineconeProjectionService(_Chunks(chunk), _Embeddings(EmbeddingResult(vector=[1, 2], specification=bad_spec)), vectors, settings)
    with pytest.raises(ValueError, match="incompatible"):
        await service.project_source_version(chunk.source_version_id)
    assert vectors.calls == []


@pytest.mark.asyncio
async def test_projection_repeated_run_reuses_canonical_identity():
    chunk = _chunk()
    settings = _settings(gemini_embedding_dimension=2)
    spec = EmbeddingSpec.from_settings(settings)
    vectors = _Vectors()
    service = PineconeProjectionService(_Chunks(chunk), _Embeddings(EmbeddingResult(vector=[1, 2], specification=spec)), vectors, settings)
    await service.project_source_version(chunk.source_version_id)
    await service.project_source_version(chunk.source_version_id)
    assert vectors.calls[0][1][0][0] == vectors.calls[1][1][0][0] == str(chunk.chunk_id)
