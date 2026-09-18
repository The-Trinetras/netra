"""Repository-owned Ragas-style retrieval metrics (Ragas stays uninstalled).

runtime-baseline.md / learning.md: implement Ragas-style metrics in
evaluation scripts instead of adding the Ragas package. These are
deterministic set/rank metrics over evidence ids, requiring human
relevant-evidence labels. Without labels the result is NOT_EVALUATED,
never 0 or 1. They measure retrieval of labelled-relevant evidence, not
factual correctness, and are not comparable with Prometheus ordinal scores.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence


@dataclass(frozen=True)
class MetricValue:
    name: str
    value: Optional[float]
    evaluated: bool
    reason: Optional[str] = None


def _not_evaluated(name: str, reason: str) -> MetricValue:
    return MetricValue(name=name, value=None, evaluated=False, reason=reason)


def context_precision(retrieved: Sequence[str], relevant: Optional[set[str]]) -> MetricValue:
    """Mean of precision@k over the ranks k where a relevant item appears
    (the rank-aware form Ragas describes), normalised by relevant hits."""

    name = "context_precision"
    if relevant is None:
        return _not_evaluated(name, "no_relevance_labels")
    if not retrieved:
        return _not_evaluated(name, "nothing_retrieved")
    hits = 0
    total = 0.0
    for rank, evidence_id in enumerate(retrieved, start=1):
        if evidence_id in relevant:
            hits += 1
            total += hits / rank
    return MetricValue(name=name, value=(total / hits) if hits else 0.0, evaluated=True)


def context_recall(retrieved: Sequence[str], relevant: Optional[set[str]]) -> MetricValue:
    name = "context_recall"
    if relevant is None:
        return _not_evaluated(name, "no_relevance_labels")
    if not relevant:
        return _not_evaluated(name, "empty_relevance_label_set")
    return MetricValue(name=name, value=len(set(retrieved) & relevant) / len(relevant), evaluated=True)


def cited_evidence_support(cited: Sequence[str], supporting: Optional[set[str]]) -> MetricValue:
    """Share of cited evidence ids a human marked as supporting the answer
    (a faithfulness-style proxy; a citation alone never proves support)."""

    name = "cited_evidence_support"
    if supporting is None:
        return _not_evaluated(name, "no_support_labels")
    if not cited:
        return _not_evaluated(name, "nothing_cited")
    return MetricValue(name=name, value=sum(1 for e in cited if e in supporting) / len(cited), evaluated=True)
