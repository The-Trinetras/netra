"""Run only the real E4 hybrid-plus-BGE retrieval benchmark."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[2]
API_SRC = ROOT / "api" / "src"
sys.path.insert(0, str(API_SRC))
sys.path.insert(0, str(ROOT / "evaluation" / "scripts"))

from netra_api.content.settings import ContentSettings, application_database_url  # noqa: E402
from netra_api.content.retrieval.factory import build_postgres_retrieval_service  # noqa: E402
from netra_api.db.models import SourceRow, SourceVersionRow  # noqa: E402
from netra_api.platform.auth_context import AuthContext  # noqa: E402
from netra_api.platform.database import create_engine, create_session_factory  # noqa: E402

from retrieval_evaluation import load_golden_cases  # noqa: E402
from retrieval_experiments import ExperimentSummary, RetrievalExperimentRunner, load_manifest  # noqa: E402
from run_real_retrieval_benchmark import _canonical_scope  # noqa: E402


MANIFEST = ROOT / "evaluation" / "manifests" / "retrieval_v1.json"
DATASET = ROOT / "evaluation" / "cases" / "netra_e3_real_golden_v1.jsonl"
RESULTS = ROOT / "evaluation" / "results"
E3_ARTIFACT = RESULTS / "e3_real_golden_v1.jsonl"


def _write_cases(path: Path, summary: ExperimentSummary) -> None:
    path.write_text("".join(item.model_dump_json() + "\n" for item in summary.cases_detail), encoding="utf-8")


def _e3_summary() -> dict:
    payload = json.loads((RESULTS / "e0_e3_real_golden_v1_summary.json").read_text(encoding="utf-8"))
    return next(item for item in payload["experiments"] if item["experiment_id"] == "E3")


def _ranking_changes(summary: ExperimentSummary) -> list[dict]:
    old = {json.loads(line)["example_id"]: json.loads(line)["retrieved_ids"]
           for line in E3_ARTIFACT.read_text(encoding="utf-8").splitlines() if line.strip()}
    return [{"case_id": item.example_id, "e3_ids": old.get(item.example_id, []),
             "e4_ids": list(item.retrieved_ids),
             "changed": old.get(item.example_id, []) != list(item.retrieved_ids)}
            for item in summary.cases_detail]


async def main() -> None:
    specs = [spec for spec in load_manifest(MANIFEST) if spec.experiment_id == "E4"]
    if len(specs) != 1:
        raise RuntimeError("manifest must contain exactly one E4 specification")
    spec = specs[0]
    if not spec.reranker_enabled or not spec.rrf_enabled:
        raise RuntimeError("E4 must enable RRF and BGE reranking")
    cases = load_golden_cases(DATASET, dataset_id=spec.dataset_id, dataset_version=spec.dataset_version)
    settings = ContentSettings()
    settings.reranker_enabled = True
    settings.reranker_model_id = "BAAI/bge-reranker-v2-m3"
    settings.reranker_batch_size = 4
    settings.reranker_device = "cpu"
    settings.vector_top_k = 20
    settings.fts_top_k = 20
    settings.rrf_k = 60
    settings.fusion_top_k = 12
    settings.rerank_top_k = 12
    settings.final_evidence_min = 4
    settings.final_evidence_max = 6
    if settings.embedding_model != "gemini-embedding-001" or settings.embedding_dimension != 1536:
        raise RuntimeError("configured embedding model/dimension does not match E4")
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured")
    if not settings.pinecone_api_key or not settings.pinecone_index_name:
        raise RuntimeError("Pinecone API key or index name is not configured")
    try:
        flagembedding_version = package_version("FlagEmbedding")
    except PackageNotFoundError:
        flagembedding_version = "unknown"

    engine = create_engine(application_database_url(), pool_size=settings.database_pool_size)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            account_id = await _canonical_scope(session, cases)
            auth = AuthContext(account_id=account_id, session_id=uuid4(), request_id=uuid4(),
                               issued_at=datetime.now(timezone.utc))
            service = build_postgres_retrieval_service(session, settings)
            if service.reranker is None:
                raise RuntimeError("E4 composition did not enable the BGE reranker")
            runner = RetrievalExperimentRunner(service.lexical, service.semantic,
                                               service.evidence_resolver, reranker=service.reranker)
            summary = await runner.run(auth, cases, spec)
    finally:
        await engine.dispose()

    RESULTS.mkdir(parents=True, exist_ok=True)
    case_path = RESULTS / "e4_real_golden_v1.jsonl"
    summary_path = RESULTS / "e4_real_golden_v1_summary.json"
    _write_cases(case_path, summary)
    e3 = _e3_summary()
    summary_path.write_text(json.dumps({
        "experiment": summary.model_dump(mode="json", exclude={"cases_detail"}),
        "model": {"name": settings.reranker_model_id, "library": "FlagEmbedding",
                  "library_version": flagembedding_version, "device": settings.reranker_device,
                  "weights_loaded": summary.status == "executed"},
        "configuration": {"vector_top_k": 20, "fts_top_k": 20, "rrf_k": 60,
                          "fusion_top_k": 12, "rerank_top_k": 12, "reranker_enabled": True,
                          "reranker_model": settings.reranker_model_id, "device": "cpu", "batch_size": 4,
                          "embedding_model": settings.embedding_model,
                          "embedding_dimension": settings.embedding_dimension},
        "e3_vs_e4": {"E3": {key: e3.get(key) for key in ("recall_at_5", "recall_at_10", "mrr", "latency_p50_ms", "latency_p95_ms")},
                     "E4": {"recall_at_5": summary.recall_at_5, "recall_at_10": summary.recall_at_10,
                            "mrr": summary.mrr, "latency_p50_ms": summary.latency_p50_ms,
                            "latency_p95_ms": summary.latency_p95_ms}},
        "ranking_changes": _ranking_changes(summary),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }, indent=2) + "\n", encoding="utf-8")
    print("E4", summary.status, summary.successful_cases, summary.failed_cases,
          summary.recall_at_5, summary.recall_at_10, summary.mrr,
          "flagembedding=" + flagembedding_version)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
