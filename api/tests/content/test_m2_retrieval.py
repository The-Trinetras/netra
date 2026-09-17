from netra_api.content.retrieval.exact_search import SearchCandidate
from netra_api.content.retrieval.ranking import reciprocal_rank_fusion


def test_rrf_deduplicates_and_prefers_consensus():
    result = reciprocal_rank_fusion(
        [SearchCandidate(evidence_id="one", score=1), SearchCandidate(evidence_id="two", score=1)],
        [SearchCandidate(evidence_id="two", score=1)],
    )
    assert [item.evidence_id for item in result] == ["two", "one"]


def test_rrf_ties_are_deterministic_by_id():
    result = reciprocal_rank_fusion([SearchCandidate(evidence_id="b", score=1), SearchCandidate(evidence_id="a", score=1)])
    assert [item.evidence_id for item in result] == ["a", "b"]


def test_rrf_uses_frozen_k_and_truncates_to_fusion_limit():
    result = reciprocal_rank_fusion([
        SearchCandidate(evidence_id=str(index), score=1) for index in range(13)
    ])
    assert len(result) == 12
    assert result[0].score == 1 / 61
