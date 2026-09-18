from uuid import uuid4

import pytest

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


@pytest.mark.asyncio
async def test_semantic_search_always_scopes_account_and_optional_versions():
    auth = _auth()
    index = Index()
    versions = [uuid4(), uuid4()]
    await PineconeSemanticSearch(Embedder(), index).search(auth, "query", versions, 99)
    assert index.args[2] == 20
    assert index.args[3] == {"account_id": str(auth.account_id),
                             "source_version_id": {"$in": [str(i) for i in versions]}}
