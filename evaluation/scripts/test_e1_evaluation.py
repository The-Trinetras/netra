import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from retrieval_evaluation import (
    DatasetLoadError, ExperimentConfig, GoldenEvaluationCase, RetrievalEvaluationResult,
    RetrievalOnlyRunner, RetrievalResultStore, aggregate_metrics, mean_reciprocal_rank,
    recall_at_k,
)


def case(**overrides):
    values = dict(dataset_id="fixture", dataset_version="1", example_id="e1", question="What?",
                  reference_evidence_ids=("a",), reference_contexts=("context",))
    values.update(overrides)
    return GoldenEvaluationCase(**values)


def test_schema_identity_and_validation():
    assert case().semantic_identity == ("fixture", "1", "e1")
    with pytest.raises(ValidationError):
        case(example_id="")
    with pytest.raises(ValidationError):
        case(unexpected="value")


def test_metrics_cover_ranks_duplicates_and_empty_reference():
    assert recall_at_k(("a", "b"), ("a", "a", "x", "b"), 5) == 1.0
    assert recall_at_k(("a", "b"), ("x",), 10) == 0.0
    assert mean_reciprocal_rank(("a",), ("x", "a")) == 0.5
    assert mean_reciprocal_rank(("a",), ("x",)) == 0.0
    assert recall_at_k((), (), 5) == 1.0
    assert mean_reciprocal_rank((), ()) == 0.0


def test_loader_is_sorted_and_rejects_duplicates(tmp_path: Path):
    path = tmp_path / "cases.jsonl"
    path.write_text("\n".join(json.dumps(case(example_id=i).model_dump(mode="json")) for i in ("b", "a")), encoding="utf-8")
    assert [item.example_id for item in __import__("retrieval_evaluation").load_golden_cases(path)] == ["a", "b"]
    path.write_text(path.read_text(encoding="utf-8") + "\n" + json.dumps(case(example_id="a").model_dump(mode="json")), encoding="utf-8")
    with pytest.raises(DatasetLoadError, match="duplicate"):
        __import__("retrieval_evaluation").load_golden_cases(path)


def test_result_storage_and_aggregation(tmp_path: Path):
    result = RetrievalEvaluationResult(experiment_id="x", experiment_version="1", dataset_id="d",
        dataset_version="1", example_id="e", retrieval_strategy="hybrid", query="q",
        retrieval_status="success", latency_ms=1, recall_at_5=1, recall_at_10=0.5, mrr=1)
    path = tmp_path / "results.jsonl"
    RetrievalResultStore.write(path, [result])
    with pytest.raises(FileExistsError):
        RetrievalResultStore.write(path, [result])
    metrics = aggregate_metrics([result, result.model_copy(update={"recall_at_5": None})])
    assert metrics.examples == 2 and metrics.recall_at_5 == 1


@pytest.mark.asyncio
async def test_retrieval_only_runner_records_ids_and_metrics():
    class Hit:
        evidence_id = "a"

    class Retrieval:
        async def search(self, auth, query):
            return [Hit()]

    result = await RetrievalOnlyRunner(Retrieval()).run(
        case(), ExperimentConfig(experiment_id="x", experiment_version="1", dataset_id="fixture",
                                 dataset_version="1", retrieval_strategy="hybrid"), object())
    assert result.retrieval_status == "success"
    assert result.retrieved_chunk_ids == ("a",)
    assert result.recall_at_5 == 1
