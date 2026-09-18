"""Opt-in local smoke test for the BGE reranker.

Run with ``NETRA_BGE_LIVE_SMOKE=1`` when model weights may be downloaded.
This is intentionally not a pytest module and never runs in the normal suite.
"""

from __future__ import annotations

import asyncio
import os
import time

from netra_api.content.settings import ContentSettings
from netra_api.content.retrieval.exact_search import SearchCandidate
from netra_api.content.retrieval.reranker import BGEReranker


async def main() -> None:
    if os.getenv("NETRA_BGE_LIVE_SMOKE") != "1":
        raise SystemExit("Set NETRA_BGE_LIVE_SMOKE=1 to explicitly enable model loading")
    settings = ContentSettings(reranker_enabled=True)
    reranker = BGEReranker(settings)
    candidates = [
        SearchCandidate(evidence_id="passage-a", score=0.0,
                        text="Binary search repeatedly halves a sorted search space."),
        SearchCandidate(evidence_id="passage-b", score=0.0,
                        text="A triangle has three sides and three angles."),
    ]
    started = time.perf_counter()
    result = await reranker.rerank("How does binary search reduce its search space?", candidates)
    elapsed_ms = (time.perf_counter() - started) * 1000
    print(f"model={settings.reranker_model_id}")
    print(f"ranked_count={len(result)}")
    print(f"top_id={result[0].evidence_id if result else 'none'}")
    print(f"elapsed_ms={elapsed_ms:.1f}")


if __name__ == "__main__":
    asyncio.run(main())
