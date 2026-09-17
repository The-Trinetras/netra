"""Run the real E0-E3 retrieval benchmark against local M2 services.

This script is intentionally an evaluation entry point, not a production
authentication implementation.  It derives the single benchmark account
from canonical PostgreSQL rows referenced by the locked dataset and refuses
to run if those references are not consistently owned.  It never uses a
global account scope or Pinecone metadata as authorization.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[2]
API_SRC = ROOT / "api" / "src"
if str(API_SRC) not in sys.path:
    sys.path.insert(0, str(API_SRC))
sys.path.insert(0, str(ROOT / "evaluation" / "scripts"))

from netra_api.config import Settings  # noqa: E402
from netra_api.content.retrieval.factory import build_postgres_retrieval_service  # noqa: E402
from netra_api.db.models import SourceRow, SourceVersionRow  # noqa: E402
from netra_api.platform.auth_context import AuthContext  # noqa: E402
from netra_api.platform.database import create_engine, create_session_factory  # noqa: E402

from retrieval_evaluation import load_golden_cases  # noqa: E402
from retrieval_experiments import (  # noqa: E402
    ExperimentSummary,
    RetrievalExperimentRunner,
    load_manifest,
)


MANIFEST = ROOT / "evaluation" / "manifests" / "retrieval_v1.json"
RESULTS = ROOT / "evaluation" / "results"
DATASET = ROOT / "evaluation" / "cases" / "netra_e3_real_golden_v1.jsonl"


async def _canonical_scope(session, cases):
    source_ids = {source_id for case in cases for source_id in case.source_ids}
    version_ids = {version_id for case in cases for version_id in case.source_version_ids}
    if not source_ids or not version_ids:
        raise RuntimeError("locked dataset has no canonical source/version scope")
    rows = (await session.execute(
        select(SourceRow.source_id, SourceRow.account_id, SourceVersionRow.source_version_id)
        .join(SourceVersionRow, SourceVersionRow.source_id == SourceRow.source_id)
        .where(SourceRow.source_id.in_(source_ids),
               SourceVersionRow.source_version_id.in_(version_ids))
    )).all()
    by_source = {row.source_id: row for row in rows}
    by_version = {row.source_version_id: row for row in rows}
    if set(by_source) != source_ids or set(by_version) != version_ids:
        raise RuntimeError("locked dataset references missing canonical source/version rows")
    accounts = {row.account_id for row in rows}
    if len(accounts) != 1:
        raise RuntimeError("locked dataset references multiple canonical accounts")
    for case in cases:
        if any(by_version[version_id].source_id not in source_ids for version_id in case.source_version_ids):
            raise RuntimeError(f"dataset case has a source/version scope mismatch: {case.example_id}")
    return next(iter(accounts))


def _write_case_artifact(path: Path, summary: ExperimentSummary) -> None:
    path.write_text(
        "".join(detail.model_dump_json() + "\n" for detail in summary.cases_detail),
        encoding="utf-8",
    )


async def main() -> None:
    specs = [spec for spec in load_manifest(MANIFEST) if spec.experiment_id in {"E0", "E1", "E2", "E3"}]
    if [spec.experiment_id for spec in specs] != ["E0", "E1", "E2", "E3"]:
        raise RuntimeError("manifest must contain E0-E3 in order")
    if any(spec.reranker_enabled for spec in specs):
        raise RuntimeError("E0-E3 must not enable reranking")
    cases = load_golden_cases(DATASET, dataset_id=specs[0].dataset_id,
                              dataset_version=specs[0].dataset_version)

    settings = Settings()
    if settings.embedding_model != "gemini-embedding-001" or settings.embedding_dimension != 1536:
        raise RuntimeError("configured embedding model/dimension does not match the locked benchmark")
    if settings.gemini_embedding_model != "gemini-embedding-001" or settings.gemini_embedding_dimension != 1536:
        raise RuntimeError("configured Gemini embedding model/dimension does not match the locked benchmark")
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured")
    if not settings.pinecone_api_key or not settings.pinecone_index_name:
        raise RuntimeError("Pinecone API key or index name is not configured")
    settings.reranker_enabled = False

    engine = create_engine(settings)
    factory = create_session_factory(engine)
    summaries: list[ExperimentSummary] = []
    try:
        async with factory() as session:
            account_id = await _canonical_scope(session, cases)
            auth = AuthContext(account_id=account_id, session_id=uuid4(), request_id=uuid4(),
                               issued_at=datetime.now(timezone.utc))
            service = build_postgres_retrieval_service(session, settings)
            runner = RetrievalExperimentRunner(service.lexical, service.semantic,
                                               service.evidence_resolver)
            for spec in specs:
                summary = await runner.run(auth, cases, spec)
                summaries.append(summary)
                _write_case_artifact(RESULTS / f"{spec.experiment_id.lower()}_real_golden_v1.jsonl", summary)
    finally:
        await engine.dispose()

    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "e0_e3_real_golden_v1_summary.json").write_text(
        json.dumps({
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "dataset": {"path": str(DATASET.relative_to(ROOT)), "cases": len(cases)},
            "experiments": [summary.model_dump(mode="json", exclude={"cases_detail"})
                            for summary in summaries],
        }, indent=2) + "\n", encoding="utf-8"
    )
    for summary in summaries:
        print(summary.experiment_id, summary.status, summary.successful_cases,
              summary.failed_cases, summary.recall_at_5, summary.mrr)


if __name__ == "__main__":
    asyncio.run(main())
