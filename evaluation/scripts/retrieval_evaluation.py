"""Deterministic, retrieval-only evaluation foundations for Netra.

This module deliberately has no RAGAS or Prometheus runtime dependency.  It
stores canonical evidence returned by the application retrieval boundary and
provides deterministic ID-based retrieval metrics.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class GoldenEvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset_id: str
    dataset_version: str
    example_id: str
    question: str
    reference_answer: str | None = None
    reference_evidence_ids: tuple[str, ...] = ()
    reference_contexts: tuple[str, ...] = ()
    source_ids: tuple[UUID, ...] = ()
    source_version_ids: tuple[UUID, ...] = ()
    expected_answer_type: str | None = None
    tags: tuple[str, ...] = ()
    notes: str | None = None

    @field_validator("dataset_id", "dataset_version", "example_id", "question")
    @classmethod
    def nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value

    @field_validator("reference_evidence_ids", "tags")
    @classmethod
    def valid_items(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value.strip() for value in values):
            raise ValueError("items must not be empty")
        return values

    @property
    def semantic_identity(self) -> tuple[str, str, str]:
        return self.dataset_id, self.dataset_version, self.example_id


class ExperimentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    experiment_id: str
    experiment_version: str
    dataset_id: str
    dataset_version: str
    retrieval_strategy: str
    retrieval_config: dict[str, Any] = Field(default_factory=dict)
    embedding_model: str | None = None
    embedding_dimension: int | None = Field(default=None, gt=0)
    reranker_model: str | None = None
    prompt_version: str | None = None


class RetrievalEvaluationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    experiment_id: str
    experiment_version: str
    dataset_id: str
    dataset_version: str
    example_id: str
    retrieval_strategy: str
    query: str
    retrieved_chunk_ids: tuple[str, ...] = ()
    retrieved_contexts: tuple[str, ...] = ()
    source_version_ids: tuple[UUID, ...] = ()
    generated_answer: str | None = None
    reference_answer: str | None = None
    reference_evidence_ids: tuple[str, ...] = ()
    reference_contexts: tuple[str, ...] = ()
    retrieval_status: str
    error_type: str | None = None
    latency_ms: float
    recall_at_5: float | None = None
    recall_at_10: float | None = None
    mrr: float | None = None
    context_precision: float | None = None
    context_recall: float | None = None
    faithfulness: float | None = None
    answer_relevancy: float | None = None
    prometheus_score: float | None = None
    prometheus_rationale: str | None = None
    evaluator_model: str | None = None
    evaluator_model_version: str | None = None
    evaluator_prompt_version: str | None = None


class DatasetLoadError(ValueError):
    pass


def load_golden_cases(path: str | Path, *, dataset_id: str | None = None,
                      dataset_version: str | None = None) -> list[GoldenEvaluationCase]:
    path = Path(path)
    if not path.is_file():
        raise DatasetLoadError(f"dataset does not exist: {path}")
    cases: list[GoldenEvaluationCase] = []
    identities: set[tuple[str, str, str]] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            raise DatasetLoadError(f"blank dataset line at {line_number}")
        try:
            case = GoldenEvaluationCase.model_validate(json.loads(line))
        except (ValueError, json.JSONDecodeError) as exc:
            raise DatasetLoadError(f"invalid case at line {line_number}: {exc}") from exc
        if dataset_id is not None and case.dataset_id != dataset_id:
            raise DatasetLoadError(f"unexpected dataset_id at line {line_number}")
        if dataset_version is not None and case.dataset_version != dataset_version:
            raise DatasetLoadError(f"unexpected dataset_version at line {line_number}")
        if case.semantic_identity in identities:
            raise DatasetLoadError(f"duplicate case identity at line {line_number}: {case.example_id}")
        identities.add(case.semantic_identity)
        cases.append(case)
    return sorted(cases, key=lambda case: case.example_id)


def recall_at_k(reference_ids: tuple[str, ...] | list[str], retrieved_ids: tuple[str, ...] | list[str], k: int) -> float:
    if not reference_ids:
        return 1.0
    return len(set(reference_ids) & set(retrieved_ids[:k])) / len(set(reference_ids))


def mean_reciprocal_rank(reference_ids: tuple[str, ...] | list[str], retrieved_ids: tuple[str, ...] | list[str]) -> float:
    relevant = set(reference_ids)
    if not relevant:
        return 0.0
    for rank, evidence_id in enumerate(retrieved_ids, 1):
        if evidence_id in relevant:
            return 1.0 / rank
    return 0.0


def _json_line(value: BaseModel) -> str:
    return json.dumps(value.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


class RetrievalResultStore:
    @staticmethod
    def write(path: str | Path, results: list[RetrievalEvaluationResult], *, overwrite: bool = False) -> None:
        path = Path(path)
        if path.exists() and not overwrite:
            raise FileExistsError(f"result file already exists: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(_json_line(result) + "\n" for result in results), encoding="utf-8")

    @staticmethod
    def append(path: str | Path, result: RetrievalEvaluationResult) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(_json_line(result) + "\n")


@dataclass(frozen=True)
class AggregatedMetrics:
    examples: int
    recall_at_5: float | None
    recall_at_10: float | None
    mrr: float | None


def _average(values: list[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return sum(present) / len(present) if present else None


def aggregate_metrics(results: list[RetrievalEvaluationResult]) -> AggregatedMetrics:
    return AggregatedMetrics(len(results), _average([r.recall_at_5 for r in results]),
                             _average([r.recall_at_10 for r in results]), _average([r.mrr for r in results]))


class RetrievalOnlyRunner:
    """Calls the existing retrieval and canonical evidence interfaces."""

    def __init__(self, retrieval_service: Any, *, evidence_resolver: Any | None = None) -> None:
        self.retrieval_service = retrieval_service
        self.evidence_resolver = evidence_resolver

    async def run(self, case: GoldenEvaluationCase, experiment: ExperimentConfig, auth: Any) -> RetrievalEvaluationResult:
        import time
        from netra_api.content.retrieval.service import RetrievalQuery

        started = time.perf_counter()
        try:
            query = RetrievalQuery(query_text=case.question,
                                   source_version_ids=list(case.source_version_ids) or None,
                                   top_k=10)
            hits = await self.retrieval_service.search(auth, query)
            ids = [hit.evidence_id for hit in hits]
            contexts: list[str] = []
            if self.evidence_resolver is not None:
                resolutions = await self.evidence_resolver.resolve(
                    auth, ids, allowed_source_version_ids=list(case.source_version_ids) or None,
                    require_active=True)
                contexts = [resolution.evidence.text for resolution in resolutions if resolution.is_resolved]
            return RetrievalEvaluationResult(
                experiment_id=experiment.experiment_id, experiment_version=experiment.experiment_version,
                dataset_id=case.dataset_id, dataset_version=case.dataset_version, example_id=case.example_id,
                retrieval_strategy=experiment.retrieval_strategy, query=case.question,
                retrieved_chunk_ids=tuple(ids), retrieved_contexts=tuple(contexts),
                source_version_ids=case.source_version_ids, reference_answer=case.reference_answer,
                reference_evidence_ids=case.reference_evidence_ids, reference_contexts=case.reference_contexts,
                retrieval_status="success", latency_ms=(time.perf_counter() - started) * 1000,
                recall_at_5=recall_at_k(case.reference_evidence_ids, ids, 5),
                recall_at_10=recall_at_k(case.reference_evidence_ids, ids, 10),
                mrr=mean_reciprocal_rank(case.reference_evidence_ids, ids))
        except Exception as exc:
            return RetrievalEvaluationResult(
                experiment_id=experiment.experiment_id, experiment_version=experiment.experiment_version,
                dataset_id=case.dataset_id, dataset_version=case.dataset_version, example_id=case.example_id,
                retrieval_strategy=experiment.retrieval_strategy, query=case.question,
                retrieval_status="error", error_type=type(exc).__name__,
                latency_ms=(time.perf_counter() - started) * 1000)


class RagasAdapter(Protocol):
    """Future adapter boundary for canonical contexts and explicit RAGAS metrics."""
    def evaluate(self, result: RetrievalEvaluationResult) -> RetrievalEvaluationResult: ...


class Prometheus2Adapter(Protocol):
    """Future offline adapter boundary; implementation/model runtime is not installed."""
    def evaluate(self, *, rubric_id: str, rubric_version: str, instruction: str,
                 response: str, reference_answer: str | None) -> dict[str, Any]: ...
