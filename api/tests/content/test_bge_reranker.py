import asyncio

import pytest

from netra_api.config import Settings
from netra_api.content.retrieval.exact_search import SearchCandidate
from netra_api.content.retrieval.reranker import BGEReranker
from netra_api.content.retrieval.service import RetrievalProviderUnavailableError


class FakeModel:
    def __init__(self, scores):
        self.scores = scores
        self.calls = []

    def compute_score(self, pairs):
        self.calls.append(pairs)
        return [self.scores[pair[1]] for pair in pairs]


def candidates(count=3):
    return [SearchCandidate(evidence_id=str(i), score=0.0, text=f"passage {i}") for i in range(count)]


@pytest.mark.asyncio
async def test_bge_reranker_batches_pairs_and_preserves_ids():
    model = FakeModel({f"passage {i}": float(i) for i in range(5)})
    reranker = BGEReranker(Settings(reranker_batch_size=2), lambda *_args, **_kwargs: model)
    result = await reranker.rerank("query", candidates(5))
    assert [item.evidence_id for item in result] == ["4", "3", "2", "1", "0"]
    assert model.calls == [
        [["query", "passage 0"], ["query", "passage 1"]],
        [["query", "passage 2"], ["query", "passage 3"]],
        [["query", "passage 4"]],
    ]


@pytest.mark.asyncio
async def test_bge_reranker_equal_scores_keep_input_order_and_load_once():
    model = FakeModel({f"passage {i}": 1.0 for i in range(3)})
    calls = 0

    def factory(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return model

    reranker = BGEReranker(model_factory=factory)
    assert [x.evidence_id for x in await reranker.rerank("q", candidates())] == ["0", "1", "2"]
    assert [x.evidence_id for x in await reranker.rerank("q", candidates())] == ["0", "1", "2"]
    assert calls == 1


@pytest.mark.asyncio
async def test_bge_reranker_bounds_candidates_and_handles_empty():
    model = FakeModel({f"passage {i}": float(i) for i in range(13)})
    reranker = BGEReranker(model_factory=lambda *_args, **_kwargs: model)
    assert await reranker.rerank("q", []) == []
    result = await reranker.rerank("q", candidates(13))
    assert len(result) == 12


@pytest.mark.asyncio
async def test_bge_reranker_rejects_missing_or_nonfinite_scores():
    class BadModel:
        def compute_score(self, _pairs):
            return [float("nan")]

    reranker = BGEReranker(model_factory=lambda *_args, **_kwargs: BadModel())
    with pytest.raises(ValueError, match="non-finite"):
        await reranker.rerank("q", candidates(1))

    missing_text = BGEReranker(model_factory=lambda *_args, **_kwargs: FakeModel({}))
    with pytest.raises(ValueError, match="passage text"):
        await missing_text.rerank("q", [SearchCandidate(evidence_id="x", score=1)])


@pytest.mark.asyncio
async def test_bge_provider_unavailable_is_explicit():
    def unavailable(*_args, **_kwargs):
        raise OSError("model cache unavailable")

    reranker = BGEReranker(model_factory=unavailable)
    with pytest.raises(RetrievalProviderUnavailableError):
        await reranker.rerank("q", candidates(1))
