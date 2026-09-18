"""Deterministic, security-aware metrics for M2 retrieval evaluation."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable


class RetrievalSecurityError(RuntimeError):
    """Raised when an evaluation run observes unauthorized final context."""


def precision_at_k(relevant: Iterable[str], retrieved: Iterable[str], k: int) -> float:
    items = list(retrieved)[:k]
    return (len(set(items) & set(relevant)) / len(items)) if items else 0.0


def recall_at_k(relevant: Iterable[str], retrieved: Iterable[str], k: int) -> float:
    truth = set(relevant)
    return 1.0 if not truth else len(truth & set(list(retrieved)[:k])) / len(truth)


def mrr(relevant: Iterable[str], retrieved: Iterable[str]) -> float:
    truth = set(relevant)
    for rank, item in enumerate(retrieved, 1):
        if item in truth:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(relevant: Iterable[str], retrieved: Iterable[str], k: int) -> float:
    truth, items = set(relevant), list(retrieved)[:k]
    dcg = sum(1.0 / math.log2(rank + 1) for rank, item in enumerate(items, 1) if item in truth)
    ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, min(k, len(truth)) + 1))
    return dcg / ideal if ideal else 1.0


@dataclass(frozen=True)
class SecurityMetrics:
    correct_source_version_rate: float
    stale_source_version_count: int
    authorized_retrieval_rate: float
    unauthorized_context_count: int

    def require_safe(self) -> None:
        if self.unauthorized_context_count:
            raise RetrievalSecurityError("unauthorized_context_count must be zero")


def security_metrics(*, expected_version_id: str | None, resolved_version_ids: Iterable[str],
                     authorized_flags: Iterable[bool]) -> SecurityMetrics:
    versions, flags = list(resolved_version_ids), list(authorized_flags)
    stale = sum(version != expected_version_id for version in versions) if expected_version_id else 0
    correct = sum(version == expected_version_id for version in versions) if expected_version_id else len(versions)
    authorized = sum(flags)
    return SecurityMetrics(
        correct_source_version_rate=(correct / len(versions) if versions else 1.0),
        stale_source_version_count=stale,
        authorized_retrieval_rate=(authorized / len(flags) if flags else 1.0),
        unauthorized_context_count=len(flags) - authorized,
    )
