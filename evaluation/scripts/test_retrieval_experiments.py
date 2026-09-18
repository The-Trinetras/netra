from pathlib import Path
from uuid import UUID

import pytest

from retrieval_evaluation import GoldenEvaluationCase
from retrieval_experiments import (ExperimentCaseResult, ExperimentSpec,
                                   RetrievalExperimentRunner, comparison_rows,
                                   load_manifest)
from netra_api.content.retrieval.exact_search import SearchCandidate


ROOT = Path(__file__).parents[2]
VERSION_ID = UUID("00000000-0000-0000-0000-000000000001")


class Search:
    def __init__(self, ids):
        self.ids = ids

    async def search(self, *_args):
        return [SearchCandidate(evidence_id=item, score=1.0, text=None) for item in self.ids]


class Resolution:
    def __init__(self, evidence_id):
        self.evidence_id = evidence_id
        self.is_resolved = True
        self.evidence = type("Evidence", (), {
            "text": f"canonical {evidence_id}",
            "source_version_id": VERSION_ID,
        })()


class Resolver:
    async def resolve(self, _auth, ids, **_kwargs):
        return [Resolution(item) for item in ids]


def test_manifest_has_explicit_frozen_matrix():
    specs = load_manifest(ROOT / "evaluation/manifests/retrieval_v1.json")
    assert [spec.experiment_id for spec in specs] == ["E0", "E1", "E2", "E3", "E4", "E5"]
    assert all(spec.vector_top_k == 20 and spec.fts_top_k == 20 and spec.rrf_k == 60
               and spec.fusion_top_k == 12 and spec.rerank_top_k == 12
               and spec.final_evidence_min == 4 and spec.final_evidence_max == 6
               for spec in specs)


@pytest.mark.asyncio
async def test_runner_reuses_existing_boundaries_for_lexical_and_hybrid_modes():
    case = GoldenEvaluationCase(
        dataset_id="d", dataset_version="1", example_id="case", question="q",
        reference_evidence_ids=("a",),
        source_version_ids=(VERSION_ID,),
    )
    base = dict(experiment_id="E0", description="x", lexical_enabled=True,
                semantic_enabled=False, rrf_enabled=False, reranker_enabled=False,
                metadata_filter_enabled=False, vector_top_k=20, fts_top_k=20,
                rrf_k=60, fusion_top_k=12, rerank_top_k=12,
                final_evidence_min=4, final_evidence_max=6,
                embedding_model="gemini-embedding-001", embedding_dimension=1536,
                reranker_model="BAAI/bge-reranker-v2-m3", dataset_path="x",
                dataset_id="d", dataset_version="1")
    runner = RetrievalExperimentRunner(Search(["a"]), Search(["b"]), Resolver())
    result = await runner.run_case(object(), case, ExperimentSpec(**base))
    assert result.status == "success"
    assert result.retrieved_ids == ("a",)
    assert result.top_5_hit and result.reciprocal_rank == 1.0

    hybrid = ExperimentSpec(**{**base, "experiment_id": "E3", "rrf_enabled": True,
                               "semantic_enabled": True})
    result = await runner.run_case(object(), case, hybrid)
    assert result.retrieved_ids[0] == "a"
    assert result.precision_at_5 == 0.5
    assert result.ndcg_at_5 > 0
    assert result.unauthorized_context_count == 0
    assert result.stale_source_version_count == 0
    assert result.retrieved_contexts[0] == "canonical a"
    assert set(result.stage_latency_ms) == {"lexical", "semantic", "fusion", "canonical_resolution"}


@pytest.mark.asyncio
async def test_bge_experiment_is_blocked_without_real_reranker():
    case = GoldenEvaluationCase(dataset_id="d", dataset_version="1", example_id="case", question="q")
    spec = load_manifest(ROOT / "evaluation/manifests/retrieval_v1.json")[4]
    summary = await RetrievalExperimentRunner(Search([]), Search([]), Resolver()).run(object(), [case], spec)
    assert summary.status == "blocked"
    assert summary.successful_cases == 0
    assert summary.cases_detail[0].error_type == "BGEUnavailable"


def test_comparison_rows_are_deterministic_and_calculated():
    def summary(experiment_id, mrr):
        return type("Summary", (), {"experiment_id": experiment_id, "recall_at_5": mrr,
            "recall_at_10": mrr, "mrr": mrr, "latency_p50_ms": 1.0,
            "latency_p95_ms": 2.0, "status": "executed"})()
    rows = comparison_rows([summary("E1", .5), summary("E0", .25)])
    assert [row["experiment_id"] for row in rows] == ["E0", "E1"]
    assert rows[1]["delta_mrr_vs_E0"] == .25
