from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from netra_api.content.retrieval.exact_search import SearchCandidate


def reciprocal_rank_fusion(*ranked_lists: Iterable[SearchCandidate], k: int = 60,
                           top_k: int = 12) -> list[SearchCandidate]:
    """Deterministic RRF with score ties resolved by evidence ID."""
    scores: dict[str, float] = defaultdict(float)
    for candidates in ranked_lists:
        previous_score = object()
        for position, candidate in enumerate(candidates, start=1):
            # Preserve equal provider scores as a tied rank. This keeps
            # reciprocal-rank scoring intact while ensuring the final score
            # tie can be resolved deterministically below.
            rank = position if candidate.score != previous_score else rank
            scores[candidate.evidence_id] += 1.0 / (k + rank)
            previous_score = candidate.score
    # Establish the deterministic secondary order first. Python's sort is
    # stable, so the score sort preserves ascending IDs when scores tie.
    ordered = sorted(scores.items(), key=lambda item: item[0])
    ordered = sorted(ordered, key=lambda item: item[1], reverse=True)
    return [SearchCandidate(evidence_id=evidence_id, score=score)
            for evidence_id, score in ordered[:top_k]]
