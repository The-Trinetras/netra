from uuid import UUID, uuid4

import pytest

from netra_api.content.retrieval.evidence import Evidence, EvidenceResolution, EvidenceRejectionReason, EvidenceTrust
from netra_api.content.retrieval.service import HybridRetrievalService, RetrievalQuery
from netra_api.content.retrieval.exact_search import SearchCandidate
from netra_api.platform.auth_context import AuthContext


def _auth(account_id: UUID) -> AuthContext:
    from datetime import datetime, timezone
    return AuthContext(account_id=account_id, session_id=uuid4(), request_id=uuid4(), issued_at=datetime.now(timezone.utc))


class _Search:
    def __init__(self, hits):
        self.hits = hits

    async def search(self, auth, query_text, source_version_ids, top_k):
        return self.hits


class _Resolver:
    def __init__(self, rejected=()):
        self.rejected = set(rejected)
        self.calls = []

    async def resolve(self, auth, evidence_ids, **kwargs):
        self.calls.append((evidence_ids, kwargs))
        return [EvidenceResolution(evidence_id=evidence_id,
                                   rejection_reason=EvidenceRejectionReason.SOURCE_VERSION_MISMATCH)
                if evidence_id in self.rejected else
                EvidenceResolution(evidence_id=evidence_id, evidence=Evidence(
                    evidence_id=evidence_id, source_version_id=uuid4(), locator="test",
                    text="canonical", provenance="postgres", trust=EvidenceTrust.SOURCE_VERIFIED))
                for evidence_id in evidence_ids]


@pytest.mark.asyncio
async def test_retrieval_applies_canonical_active_and_scope_gate():
    account = uuid4()
    version = uuid4()
    resolver = _Resolver(rejected={"stale"})
    service = HybridRetrievalService(
        _Search([SearchCandidate(evidence_id="active", score=1)]),
        _Search([SearchCandidate(evidence_id="stale", score=1)]),
        resolver,
    )
    result = await service.search(_auth(account), RetrievalQuery(query_text="q", source_version_ids=[version], top_k=5))
    assert [hit.evidence_id for hit in result] == ["active"]
    assert resolver.calls[0][1] == {"allowed_source_version_ids": [version], "require_active": True}


@pytest.mark.asyncio
async def test_retrieval_rejects_inaccessible_or_deleted_projection_ids():
    resolver = _Resolver(rejected={"deleted", "other-account"})
    service = HybridRetrievalService(
        _Search([SearchCandidate(evidence_id="deleted", score=1), SearchCandidate(evidence_id="other-account", score=.9)]),
        _Search([]), resolver,
    )
    result = await service.search(_auth(uuid4()), RetrievalQuery(query_text="q"))
    assert result == []
