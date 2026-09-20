"""Reproducible retrieval experiment matrix built on Netra's M2 boundaries.

This module composes existing lexical, semantic, RRF, reranker, and evidence
interfaces. It does not alter production settings or substitute a passthrough
reranker for an experiment that claims to measure BGE.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from netra_api.content.retrieval.exact_search import SearchCandidate
from netra_api.content.retrieval.ranking import reciprocal_rank_fusion
from netra_api.content.retrieval.service import RetrievalProviderUnavailableError
from hybrid_metrics import (ndcg_at_k, precision_at_k, security_metrics)
from retrieval_evaluation import (GoldenEvaluationCase, RetrievalResultStore,
                                   aggregate_metrics, mean_reciprocal_rank, recall_at_k)
from ragas_style import context_precision as ragas_context_precision, context_recall as ragas_context_recall


class ExperimentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    experiment_id: str
    description: str
    lexical_enabled: bool
    semantic_enabled: bool
    rrf_enabled: bool
    reranker_enabled: bool
    metadata_filter_enabled: bool
    vector_top_k: int = Field(ge=1)
    fts_top_k: int = Field(ge=1)
    rrf_k: int = Field(ge=1)
    fusion_top_k: int = Field(ge=1)
    rerank_top_k: int = Field(ge=1)
    final_evidence_min: int = Field(ge=1)
    final_evidence_max: int = Field(ge=1)
    embedding_model: str
    embedding_dimension: int = Field(gt=0)
    reranker_model: str
    dataset_path: str
    dataset_id: str
    dataset_version: str

    @field_validator("experiment_id", "description", "embedding_model", "reranker_model",
                     "dataset_path", "dataset_id", "dataset_version")
    @classmethod
    def nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value

    @field_validator("final_evidence_max")
    @classmethod
    def minimum_not_above_maximum(cls, value: int, info):
        minimum = info.data.get("final_evidence_min")
        if minimum is not None and minimum > value:
            raise ValueError("final_evidence_min must not exceed final_evidence_max")
        return value

    @property
    def configuration_snapshot(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class ExperimentCaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    experiment_id: str
    dataset_id: str
    dataset_version: str
    example_id: str
    status: str
    retrieved_ids: tuple[str, ...] = ()
    expected_ids: tuple[str, ...] = ()
    top_5_hit: bool = False
    top_10_hit: bool = False
    reciprocal_rank: float = 0.0
    precision_at_5: float = 0.0
    ndcg_at_5: float = 0.0
    retrieved_contexts: tuple[str, ...] = ()
    resolved_source_version_ids: tuple[str, ...] = ()
    unauthorized_context_count: int = Field(default=0, ge=0)
    stale_source_version_count: int = Field(default=0, ge=0)
    correct_source_version_rate: float = Field(default=1.0, ge=0, le=1)
    authorized_retrieval_rate: float = Field(default=1.0, ge=0, le=1)
    latency_ms: float = Field(ge=0)
    stage_latency_ms: dict[str, float] = Field(default_factory=dict)
    error_type: str | None = None
    fallback_used: bool = False


class ExperimentSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    experiment_id: str
    description: str
    dataset_id: str
    dataset_version: str
    status: str
    cases: int = Field(ge=0)
    successful_cases: int = Field(ge=0)
    failed_cases: int = Field(ge=0)
    recall_at_5: float | None = None
    recall_at_10: float | None = None
    mrr: float | None = None
    precision_at_5: float | None = None
    ndcg_at_5: float | None = None
    context_precision: float | None = None
    """Ragas-style rank-aware context precision (ragas_style), not precision_at_5."""
    context_recall: float | None = None
    """Ragas-style context recall over the labelled relevant evidence."""
    ragas_style_evaluated_cases: int = Field(default=0, ge=0)
    """Successful cases the two Ragas-style metrics could be computed on."""
    correct_source_version_rate: float | None = None
    authorized_retrieval_rate: float | None = None
    stale_source_version_count: int = Field(default=0, ge=0)
    unauthorized_context_count: int = Field(default=0, ge=0)
    error_rate: float = Field(default=0.0, ge=0, le=1)
    latency_p50_ms: float | None = None
    latency_p95_ms: float | None = None
    configuration: dict[str, Any] = Field(default_factory=dict)
    cases_detail: tuple[ExperimentCaseResult, ...] = ()


def _ragas_style_summary(successful: list) -> dict[str, Any]:
    """Ragas-style context metrics over the cases that carry relevance labels.

    A case without labels is not evaluated rather than counted as 0.0, so the
    denominator is reported next to the averages.
    """

    precisions, recalls = [], []
    for detail in successful:
        relevant = set(detail.expected_ids) or None
        precision = ragas_context_precision(detail.retrieved_ids, relevant)
        recall = ragas_context_recall(detail.retrieved_ids, relevant)
        if precision.evaluated:
            precisions.append(precision.value)
        if recall.evaluated:
            recalls.append(recall.value)
    return {
        "context_precision": sum(precisions) / len(precisions) if precisions else None,
        "context_recall": sum(recalls) / len(recalls) if recalls else None,
        "ragas_style_evaluated_cases": len(precisions),
    }


def load_manifest(path: str | Path) -> list[ExperimentSpec]:
    path = Path(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("experiment manifest must be a JSON array")
    specs = [ExperimentSpec.model_validate(item) for item in payload]
    if len({spec.experiment_id for spec in specs}) != len(specs):
        raise ValueError("experiment IDs must be unique")
    return specs


def _merge(left: list[SearchCandidate], right: list[SearchCandidate], top_k: int) -> list[SearchCandidate]:
    result: list[SearchCandidate] = []
    seen: set[str] = set()
    for candidate in [*left, *right]:
        if candidate.evidence_id not in seen:
            seen.add(candidate.evidence_id)
            result.append(candidate)
    return result[:top_k]


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((len(ordered) - 1) * percentile))
    return ordered[index]


class RetrievalExperimentRunner:
    """Run one explicit experiment using injected existing boundaries."""

    def __init__(self, lexical, semantic, evidence_resolver, *, reranker=None) -> None:
        self.lexical = lexical
        self.semantic = semantic
        self.evidence_resolver = evidence_resolver
        self.reranker = reranker

    async def run_case(self, auth: Any, case: GoldenEvaluationCase,
                       spec: ExperimentSpec) -> ExperimentCaseResult:
        started = time.perf_counter()
        stages: dict[str, float] = {}
        try:
            lexical: list[SearchCandidate] = []
            semantic: list[SearchCandidate] = []
            if spec.lexical_enabled:
                stage = time.perf_counter()
                lexical = await self.lexical.search(auth, case.question,
                                                    list(case.source_version_ids) or None,
                                                    spec.fts_top_k)
                stages["lexical"] = (time.perf_counter() - stage) * 1000
            if spec.semantic_enabled:
                stage = time.perf_counter()
                semantic = await self.semantic.search(auth, case.question,
                                                      list(case.source_version_ids) or None,
                                                      spec.vector_top_k)
                stages["semantic"] = (time.perf_counter() - stage) * 1000
            stage = time.perf_counter()
            if spec.rrf_enabled:
                candidates = reciprocal_rank_fusion(lexical, semantic, k=spec.rrf_k,
                                                    top_k=spec.fusion_top_k)
            else:
                candidates = _merge(lexical, semantic, spec.fusion_top_k)
            stages["fusion"] = (time.perf_counter() - stage) * 1000
            fallback = False
            if spec.reranker_enabled:
                if self.reranker is None:
                    return self._blocked(spec, case, started, stages, "BGEUnavailable")
                candidates = candidates[:spec.rerank_top_k]
                stage = time.perf_counter()
                resolutions = await self.evidence_resolver.resolve(
                    auth, [item.evidence_id for item in candidates],
                    allowed_source_version_ids=list(case.source_version_ids) or None,
                    require_active=True,
                )
                text_by_id = {item.evidence_id: item.evidence.text for item in resolutions
                              if item.is_resolved and item.evidence is not None}
                candidates = [item.model_copy(update={"text": text_by_id[item.evidence_id]})
                              for item in candidates if item.evidence_id in text_by_id]
                try:
                    candidates = await self.reranker.rerank(case.question, candidates)
                except RetrievalProviderUnavailableError:
                    fallback = True
                stages["reranking"] = (time.perf_counter() - stage) * 1000
            candidates = candidates[:spec.final_evidence_max]
            stage = time.perf_counter()
            resolutions = await self.evidence_resolver.resolve(
                auth, [item.evidence_id for item in candidates],
                allowed_source_version_ids=list(case.source_version_ids) or None,
                require_active=True,
            )
            stages["canonical_resolution"] = (time.perf_counter() - stage) * 1000
            resolved = [
                (item, resolution.evidence)
                for item, resolution in zip(candidates, resolutions)
                if resolution.is_resolved and resolution.evidence is not None
            ]
            ids = tuple(item.evidence_id for item, _evidence in resolved)
            contexts = tuple(evidence.text for _item, evidence in resolved)
            versions = tuple(str(evidence.source_version_id) for _item, evidence in resolved)
            expected_version = (
                str(case.source_version_ids[0]) if len(case.source_version_ids) == 1 else None
            )
            security = security_metrics(
                expected_version_id=expected_version,
                resolved_version_ids=versions,
                authorized_flags=(True for _item, _evidence in resolved),
            )
            security.require_safe()
            if security.stale_source_version_count:
                raise RuntimeError("retrieval returned stale source-version evidence")
            return ExperimentCaseResult(
                experiment_id=spec.experiment_id, dataset_id=case.dataset_id,
                dataset_version=case.dataset_version, example_id=case.example_id,
                status="success", retrieved_ids=ids, expected_ids=case.reference_evidence_ids,
                top_5_hit=bool(set(case.reference_evidence_ids) & set(ids[:5])),
                top_10_hit=bool(set(case.reference_evidence_ids) & set(ids[:10])),
                reciprocal_rank=mean_reciprocal_rank(case.reference_evidence_ids, ids),
                precision_at_5=precision_at_k(case.reference_evidence_ids, ids, 5),
                ndcg_at_5=ndcg_at_k(case.reference_evidence_ids, ids, 5),
                retrieved_contexts=contexts,
                resolved_source_version_ids=versions,
                unauthorized_context_count=security.unauthorized_context_count,
                stale_source_version_count=security.stale_source_version_count,
                correct_source_version_rate=security.correct_source_version_rate,
                authorized_retrieval_rate=security.authorized_retrieval_rate,
                latency_ms=(time.perf_counter() - started) * 1000,
                stage_latency_ms=stages, fallback_used=fallback,
            )
        except Exception as exc:
            return ExperimentCaseResult(
                experiment_id=spec.experiment_id, dataset_id=case.dataset_id,
                dataset_version=case.dataset_version, example_id=case.example_id,
                status="error", latency_ms=(time.perf_counter() - started) * 1000,
                stage_latency_ms=stages, error_type=type(exc).__name__,
            )

    @staticmethod
    def _blocked(spec, case, started, stages, error_type):
        return ExperimentCaseResult(
            experiment_id=spec.experiment_id, dataset_id=case.dataset_id,
            dataset_version=case.dataset_version, example_id=case.example_id,
            status="blocked", latency_ms=(time.perf_counter() - started) * 1000,
            stage_latency_ms=stages, error_type=error_type,
        )

    async def run(self, auth: Any, cases: list[GoldenEvaluationCase],
                  spec: ExperimentSpec) -> ExperimentSummary:
        details = tuple([await self.run_case(auth, case, spec) for case in cases])
        successful = [detail for detail in details if detail.status == "success"]
        if spec.reranker_enabled and self.reranker is None:
            status = "blocked"
        elif len(successful) != len(details):
            status = "partial"
        else:
            status = "executed"
        return ExperimentSummary(
            experiment_id=spec.experiment_id, description=spec.description,
            dataset_id=spec.dataset_id, dataset_version=spec.dataset_version,
            status=status, cases=len(details), successful_cases=len(successful),
            failed_cases=len(details) - len(successful),
            recall_at_5=(sum(recall_at_k(d.expected_ids, d.retrieved_ids, 5) for d in successful) / len(successful)
                         if successful else None),
            recall_at_10=(sum(recall_at_k(d.expected_ids, d.retrieved_ids, 10) for d in successful) / len(successful)
                          if successful else None),
            mrr=(sum(d.reciprocal_rank for d in successful) / len(successful) if successful else None),
            precision_at_5=(sum(d.precision_at_5 for d in successful) / len(successful)
                            if successful else None),
            ndcg_at_5=(sum(d.ndcg_at_5 for d in successful) / len(successful)
                       if successful else None),
            **_ragas_style_summary(successful),
            correct_source_version_rate=(
                sum(d.correct_source_version_rate for d in successful) / len(successful)
                if successful else None
            ),
            authorized_retrieval_rate=(
                sum(d.authorized_retrieval_rate for d in successful) / len(successful)
                if successful else None
            ),
            stale_source_version_count=sum(d.stale_source_version_count for d in successful),
            unauthorized_context_count=sum(d.unauthorized_context_count for d in successful),
            error_rate=((len(details) - len(successful)) / len(details) if details else 0.0),
            latency_p50_ms=_percentile([d.latency_ms for d in successful], .50),
            latency_p95_ms=_percentile([d.latency_ms for d in successful], .95),
            configuration=spec.configuration_snapshot, cases_detail=details,
        )


def write_summaries(path: str | Path, summaries: list[ExperimentSummary], *, overwrite: bool = False) -> None:
    path = Path(path)
    if path.exists() and not overwrite:
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(summary.model_dump_json(exclude_none=True, by_alias=False) + "\n"
                              for summary in summaries), encoding="utf-8")


def comparison_rows(summaries: list[ExperimentSummary]) -> list[dict[str, Any]]:
    baseline = next((item for item in summaries if item.experiment_id == "E0"), None)
    rows = []
    for item in sorted(summaries, key=lambda value: value.experiment_id):
        row = {"experiment_id": item.experiment_id, "recall_at_5": item.recall_at_5,
               "recall_at_10": item.recall_at_10, "mrr": item.mrr,
               "precision_at_5": getattr(item, "precision_at_5", None),
               "ndcg_at_5": getattr(item, "ndcg_at_5", None),
               "p50_latency_ms": item.latency_p50_ms, "p95_latency_ms": item.latency_p95_ms,
               "status": item.status}
        if baseline is not None and item.experiment_id != "E0":
            row["delta_mrr_vs_E0"] = (item.mrr - baseline.mrr
                                       if item.mrr is not None and baseline.mrr is not None else None)
        rows.append(row)
    return rows
