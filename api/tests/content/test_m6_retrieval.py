from uuid import uuid4

import pytest

from netra_api.content.retrieval.embeddings import EmbeddingSpec

from netra_api.content.settings import ContentSettings
from netra_api.content.retrieval.exact_search import SearchCandidate
from netra_api.content.retrieval.semantic_search import PineconeSemanticSearch
from netra_api.content.retrieval.service import (HybridRetrievalService, RetrievalProviderUnavailableError,
                                                  RetrievalQuery, RetrievalUnavailableError)
from netra_api.platform.auth_context import AuthContext


def _auth():
    from datetime import datetime, timezone
    return AuthContext(account_id=uuid4(), session_id=uuid4(), request_id=uuid4(),
                       issued_at=datetime.now(timezone.utc))


class Search:
    def __init__(self, hits):
        self.hits, self.top_k = hits, None

    async def search(self, auth, query_text, source_version_ids, top_k):
        self.top_k = top_k
        return self.hits


class FailingSearch:
    def __init__(self, error): self.error = error

    async def search(self, *args):
        raise self.error


class Resolver:
    async def resolve(self, auth, evidence_ids, **kwargs):
        from netra_api.content.retrieval.evidence import Evidence, EvidenceResolution, EvidenceTrust
        return [EvidenceResolution(evidence_id=evidence_id, evidence=Evidence(
            evidence_id=evidence_id, source_version_id=uuid4(), locator="x", text="canonical",
            provenance="postgres", trust=EvidenceTrust.SOURCE_VERIFIED)) for evidence_id in evidence_ids]


class Reranker:
    def __init__(self): self.received = None

    async def rerank(self, query_text, candidates):
        self.received = list(candidates)
        return list(reversed(candidates))


@pytest.mark.asyncio
async def test_hybrid_pipeline_uses_frozen_limits_and_final_maximum():
    lexical = Search([SearchCandidate(evidence_id=str(i), score=1) for i in range(20)])
    semantic = Search([SearchCandidate(evidence_id=str(i), score=1) for i in range(20, 40)])
    reranker = Reranker()
    service = HybridRetrievalService(lexical, semantic, Resolver(), reranker,
                                     ContentSettings())
    result = await service.search(_auth(), RetrievalQuery(query_text="query", top_k=50))
    assert lexical.top_k == 20
    assert semantic.top_k == 20
    assert len(reranker.received) == 12
    assert len(result) == 6


@pytest.mark.asyncio
async def test_semantic_unavailable_keeps_lexical_results():
    lexical = Search([SearchCandidate(evidence_id="lexical", score=1)])
    service = HybridRetrievalService(lexical, FailingSearch(RetrievalProviderUnavailableError()), Resolver())
    assert [hit.evidence_id for hit in await service.search(_auth(), RetrievalQuery(query_text="query"))] == ["lexical"]


@pytest.mark.asyncio
async def test_lexical_unavailable_keeps_semantic_results():
    semantic = Search([SearchCandidate(evidence_id="semantic", score=1)])
    service = HybridRetrievalService(FailingSearch(RetrievalProviderUnavailableError()), semantic, Resolver())
    assert [hit.evidence_id for hit in await service.search(_auth(), RetrievalQuery(query_text="query"))] == ["semantic"]


@pytest.mark.asyncio
async def test_both_provider_unavailable_is_distinguishable():
    unavailable = RetrievalProviderUnavailableError("provider down")
    service = HybridRetrievalService(FailingSearch(unavailable), FailingSearch(unavailable), Resolver())
    with pytest.raises(RetrievalUnavailableError):
        await service.search(_auth(), RetrievalQuery(query_text="query"))


@pytest.mark.asyncio
async def test_unexpected_provider_exception_propagates():
    service = HybridRetrievalService(FailingSearch(ValueError("bad internal state")), Search([]), Resolver())
    with pytest.raises(ValueError, match="bad internal state"):
        await service.search(_auth(), RetrievalQuery(query_text="query"))


def test_query_normalizes_to_frozen_final_range():
    assert RetrievalQuery(query_text="q", top_k=1).top_k == 4
    assert RetrievalQuery(query_text="q", top_k=50).top_k == 6


class Embedder:
    async def embed_query(self, text):
        return [0.1]


class Index:
    def __init__(self): self.args = None

    async def query(self, namespace, embedding, top_k, metadata_filter=None):
        self.args = namespace, embedding, top_k, metadata_filter
        return []


SPEC_1 = EmbeddingSpec(model="gemini-embedding-001", dimension=1, version="gemini-embedding-001:1")


@pytest.mark.asyncio
async def test_semantic_search_always_scopes_account_spec_and_optional_versions():
    auth = _auth()
    index = Index()
    versions = [uuid4(), uuid4()]
    await PineconeSemanticSearch(Embedder(), index, spec=SPEC_1).search(auth, "query", versions, 99)
    assert index.args[2] == 20
    assert index.args[3] == {"account_id": str(auth.account_id), "embedding_spec": SPEC_1.version,
                             "source_version_id": {"$in": [str(i) for i in versions]}}


@pytest.mark.asyncio
async def test_query_embedding_with_another_dimension_is_refused():
    from netra_api.content.retrieval.semantic_search import IncompatibleEmbeddingError

    spec_3 = EmbeddingSpec(model="gemini-embedding-001", dimension=3, version="gemini-embedding-001:3")
    with pytest.raises(IncompatibleEmbeddingError):
        await PineconeSemanticSearch(Embedder(), Index(), spec=spec_3).search(_auth(), "query", None, 5)


@pytest.mark.asyncio
async def test_matches_from_another_spec_or_account_are_dropped_even_if_the_index_returns_them():
    from netra_api.content.providers.pinecone import VectorMatch

    auth = _auth()

    class LeakyIndex(Index):
        async def query(self, namespace, embedding, top_k, metadata_filter=None):
            await super().query(namespace, embedding, top_k, metadata_filter)
            return [
                VectorMatch(id="ok", score=0.9, metadata={"embedding_spec": SPEC_1.version,
                                                          "account_id": str(auth.account_id)}),
                VectorMatch(id="old-spec", score=0.95, metadata={"embedding_spec": "gemini-embedding-001:768",
                                                                 "account_id": str(auth.account_id)}),
                VectorMatch(id="other-account", score=0.99, metadata={"embedding_spec": SPEC_1.version,
                                                                      "account_id": str(uuid4())}),
            ]

    hits = await PineconeSemanticSearch(Embedder(), LeakyIndex(), spec=SPEC_1).search(auth, "query", None, 5)
    assert [hit.evidence_id for hit in hits] == ["ok"]


@pytest.mark.asyncio
async def test_empty_version_scope_searches_nothing():
    index = Index()
    assert await PineconeSemanticSearch(Embedder(), index, spec=SPEC_1).search(_auth(), "query", [], 5) == []
    assert index.args is None
