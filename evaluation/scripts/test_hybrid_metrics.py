import pytest

from hybrid_metrics import (RetrievalSecurityError, ndcg_at_k, precision_at_k, recall_at_k,
                            mrr, security_metrics)


def test_rank_metrics_are_deterministic():
    assert precision_at_k(("a", "b"), ("a", "x"), 2) == .5
    assert recall_at_k(("a", "b"), ("a", "x"), 2) == .5
    assert mrr(("b",), ("x", "b")) == .5
    assert ndcg_at_k(("a", "b"), ("a", "x", "b"), 3) < 1


def test_unauthorized_context_is_a_hard_failure():
    metrics = security_metrics(expected_version_id="v2", resolved_version_ids=("v2", "v1"), authorized_flags=(True, False))
    assert metrics.stale_source_version_count == 1
    assert metrics.unauthorized_context_count == 1
    with pytest.raises(RetrievalSecurityError): metrics.require_safe()
