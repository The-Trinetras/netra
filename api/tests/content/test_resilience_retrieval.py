"""Canonical evidence and provider-failure resilience tests."""

from uuid import uuid4

import pytest

from netra_api.content.retrieval.evidence import Evidence, EvidenceResolution, EvidenceTrust
from netra_api.content.retrieval.exact_search import SearchCandidate
from netra_api.content.retrieval.service import (HybridRetrievalService, RetrievalProviderUnavailableError,
                                                  RetrievalQuery, RetrievalUnavailableError)
from netra_api.platform.auth_context import AuthContext
from datetime import datetime, timezone


def _auth(account=None):
    return AuthContext(account_id=account or uuid4(), session_id=uuid4(), request_id=uuid4(),
                       issued_at=datetime.now(timezone.utc))


class Search:
    def __init__(self, hits=None, error=None): self.hits, self.error = hits or [], error
    async def search(self, *_):
        if self.error: raise self.error
        return self.hits


class CanonicalResolver:
    def __init__(self, valid): self.valid = set(valid)
    async def resolve(self, _auth, evidence_ids, **_kwargs):
        return [EvidenceResolution(
            evidence_id=evidence_id,
            evidence=Evidence(evidence_id=evidence_id, source_version_id=uuid4(), locator="p1",
                              text="canonical", provenance="postgres", trust=EvidenceTrust.SOURCE_VERIFIED)
            if evidence_id in self.valid else None,
        ) for evidence_id in evidence_ids]


@pytest.mark.asyncio
async def test_stale_deleted_and_cross_account_candidates_are_rejected_by_canonical_resolver():
    candidates = [SearchCandidate(evidence_id=item, score=1) for item in ("stale", "deleted", "other-account", "active")]
    service = HybridRetrievalService(Search(candidates), Search([]),
                                     CanonicalResolver({"active"}))
    result = await service.search(_auth(), RetrievalQuery(query_text="query"))
    assert [item.evidence_id for item in result] == ["active"]


@pytest.mark.asyncio
async def test_unexpected_provider_failure_is_not_silently_reduced():
    service = HybridRetrievalService(Search(error=RuntimeError("provider bug")), Search([]), CanonicalResolver(set()))
    with pytest.raises(RuntimeError, match="provider bug"):
        await service.search(_auth(), RetrievalQuery(query_text="query"))


@pytest.mark.asyncio
async def test_both_explicitly_unavailable_providers_raise_retrieval_unavailable():
    unavailable = RetrievalProviderUnavailableError("down")
    service = HybridRetrievalService(Search(error=unavailable), Search(error=unavailable), CanonicalResolver(set()))
    with pytest.raises(RetrievalUnavailableError):
        await service.search(_auth(), RetrievalQuery(query_text="query"))
