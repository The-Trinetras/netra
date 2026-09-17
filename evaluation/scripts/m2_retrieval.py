"""RAGAS-compatible retrieval experiment input/output scaffold.

The script intentionally does not import RAGAS: it is not an approved runtime
dependency. Datasets can record retrieved evidence IDs and reference IDs, then
be evaluated in the separately managed evaluation environment.
"""

from __future__ import annotations

from dataclasses import dataclass

# Compatibility exports for the original small evaluation scaffold.  The
# retrieval benchmark schemas live in retrieval_evaluation.py.
from retrieval_evaluation import (  # noqa: F401
    ExperimentConfig, GoldenEvaluationCase, RetrievalEvaluationResult,
    RetrievalOnlyRunner, RetrievalResultStore, aggregate_metrics,
    load_golden_cases, mean_reciprocal_rank, recall_at_k,
)


@dataclass(frozen=True)
class RetrievalEvaluationCase:
    question: str
    required_evidence_ids: tuple[str, ...]
    retrieved_evidence_ids: tuple[str, ...]

    def recall_at_5(self) -> float:
        if not self.required_evidence_ids:
            return 1.0
        return len(set(self.required_evidence_ids) & set(self.retrieved_evidence_ids[:5])) / len(self.required_evidence_ids)
