"""Bounded local BGE reranking for retrieval candidates.

The model is loaded lazily and kept process-local. Importing this module does
not import FlagEmbedding or download model weights, which keeps the normal
test suite and reduced retrieval mode dependency-safe.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable
from typing import Any, Protocol, Sequence

from netra_api.content.settings import ContentSettings
from netra_api.content.retrieval.exact_search import SearchCandidate
from netra_api.content.retrieval.service import RetrievalProviderUnavailableError


class Reranker(Protocol):
    async def rerank(self, query_text: str, candidates: Sequence[SearchCandidate]) -> list[SearchCandidate]:
        ...


class PassthroughReranker:
    """Dependency-free test/demonstration implementation of the contract."""

    async def rerank(self, query_text: str, candidates: Sequence[SearchCandidate]) -> list[SearchCandidate]:
        del query_text
        return list(candidates[:12])


class BGEReranker:
    """Async adapter around FlagEmbedding's BGE cross-encoder.

    ``FlagReranker.compute_score`` is synchronous, so inference is bounded
    and moved off the event loop. Equal scores retain the deterministic input
    order established by RRF.
    """

    def __init__(self, settings: ContentSettings | None = None,
                 model_factory: Callable[..., Any] | None = None) -> None:
        self.settings = settings or ContentSettings()
        self._model_factory = model_factory
        self._model: Any | None = None
        self._model_lock = asyncio.Lock()
        self._inference_gate = asyncio.Semaphore(self.settings.reranker_max_concurrency)

    async def _get_model(self) -> Any:
        if self._model is not None:
            return self._model
        async with self._model_lock:
            if self._model is not None:
                return self._model
            factory = self._model_factory
            if factory is None:
                try:
                    from FlagEmbedding import FlagReranker
                except ImportError as exc:
                    raise RetrievalProviderUnavailableError(
                        "FlagEmbedding is unavailable for BGE reranking"
                    ) from exc
                factory = FlagReranker
            try:
                options = {"use_fp16": self.settings.reranker_device != "cpu"}
                if self.settings.reranker_device != "cpu":
                    options["devices"] = [self.settings.reranker_device]
                self._model = factory(self.settings.reranker_model_id, **options)
            except (ImportError, OSError) as exc:
                raise RetrievalProviderUnavailableError(
                    "BGE reranker model is unavailable"
                ) from exc
            return self._model

    @staticmethod
    def _scores(value: Any, expected: int) -> list[float]:
        if hasattr(value, "tolist"):
            value = value.tolist()
        if isinstance(value, (int, float)):
            value = [value]
        if not isinstance(value, (list, tuple)) or len(value) != expected:
            raise ValueError("BGE reranker returned an invalid score count")
        scores: list[float] = []
        for score in value:
            if isinstance(score, (list, tuple)):
                raise ValueError("BGE reranker returned non-scalar scores")
            try:
                score = float(score)
            except (TypeError, ValueError) as exc:
                raise ValueError("BGE reranker returned a non-numeric score") from exc
            if not math.isfinite(score):
                raise ValueError("BGE reranker returned a non-finite score")
            scores.append(score)
        return scores

    async def rerank(self, query_text: str,
                     candidates: Sequence[SearchCandidate]) -> list[SearchCandidate]:
        bounded = list(candidates[:12])
        if not bounded:
            return []
        model = await self._get_model()
        if any(candidate.text is None for candidate in bounded):
            raise ValueError("BGE reranking requires canonical passage text")
        pairs = [[query_text, candidate.text] for candidate in bounded]
        async with self._inference_gate:
            scores: list[float] = []
            for start in range(0, len(pairs), self.settings.reranker_batch_size):
                batch = pairs[start:start + self.settings.reranker_batch_size]
                result = await asyncio.to_thread(model.compute_score, batch)
                scores.extend(self._scores(result, len(batch)))
        ranked = [SearchCandidate(evidence_id=candidate.evidence_id, score=score, text=candidate.text)
                  for candidate, score in zip(bounded, scores)]
        return [item for _, item in sorted(enumerate(ranked),
                                           key=lambda pair: (-pair[1].score, pair[0]))]
