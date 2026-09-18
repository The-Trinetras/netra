"""Run one M2 retrieval experiment (retrieval metrics only).

Model judging is M4's Modal/Prometheus workflow (evaluation/scripts/judge_*.py);
this script produces retrieval results and canonical contexts it can consume.
Artifacts record source/version identity but never the account identifier.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "api" / "src"))
sys.path.insert(0, str(ROOT / "evaluation" / "scripts"))

from netra_api.content.settings import ContentSettings, application_database_url  # noqa: E402
from netra_api.content.retrieval.factory import build_postgres_retrieval_service  # noqa: E402
from netra_api.platform.auth_context import AuthContext  # noqa: E402
from netra_api.platform.database import create_engine, create_session_factory  # noqa: E402

from hybrid_health import run_checks  # noqa: E402
from retrieval_evaluation import GoldenEvaluationCase, load_golden_cases  # noqa: E402
from retrieval_experiments import (  # noqa: E402
    ExperimentSummary,
    RetrievalExperimentRunner,
    load_manifest,
)
from run_real_retrieval_benchmark import _canonical_scope  # noqa: E402

DEFAULT_DATASET = ROOT / "evaluation" / "cases" / "netra_e3_real_golden_v1.jsonl"
DEFAULT_MANIFEST = ROOT / "evaluation" / "manifests" / "retrieval_v1.json"


def _hard_failure(summary: ExperimentSummary) -> bool:
    return (
        summary.status != "executed"
        or summary.failed_cases > 0
        or summary.unauthorized_context_count > 0
        or summary.stale_source_version_count > 0
    )


async def run(args: argparse.Namespace) -> int:
    specs = {spec.experiment_id: spec for spec in load_manifest(args.manifest)}
    if args.experiment not in specs:
        raise ValueError(f"experiment is not present in manifest: {args.experiment}")
    spec = specs[args.experiment]
    cases = load_golden_cases(
        args.dataset,
        dataset_id=spec.dataset_id,
        dataset_version=spec.dataset_version,
    )

    if args.health_s3_key:
        checks = await run_checks(s3_key=args.health_s3_key)
        if any(not check.ok and check.required for check in checks):
            print("health=FAIL", file=sys.stderr)
            return 2

    settings = ContentSettings()
    settings.reranker_enabled = spec.reranker_enabled
    if settings.embedding_model != spec.embedding_model:
        raise ValueError("runtime embedding model does not match experiment manifest")
    if settings.embedding_dimension != spec.embedding_dimension:
        raise ValueError("runtime embedding dimension does not match experiment manifest")

    engine = create_engine(application_database_url(), pool_size=settings.database_pool_size)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            account_id = await _canonical_scope(session, cases)
            auth = AuthContext(
                account_id=account_id,
                session_id=uuid4(),
                request_id=uuid4(),
                issued_at=datetime.now(timezone.utc),
            )
            service = build_postgres_retrieval_service(session, settings)
            runner = RetrievalExperimentRunner(
                service.lexical,
                service.semantic,
                service.evidence_resolver,
                reranker=service.reranker,
            )
            summary = await runner.run(auth, cases, spec)
    finally:
        await engine.dispose()

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = args.output_dir or ROOT / "evaluation" / "results" / run_id
    output.mkdir(parents=True, exist_ok=False)
    (output / "cases.jsonl").write_text(
        "".join(item.model_dump_json() + "\n" for item in summary.cases_detail),
        encoding="utf-8",
    )
    (output / "summary.json").write_text(
        json.dumps(
            {
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "source_ids": sorted({str(item) for case in cases for item in case.source_ids}),
                "source_version_ids": sorted(
                    {str(item) for case in cases for item in case.source_version_ids}
                ),
                "summary": summary.model_dump(mode="json", exclude={"cases_detail"}),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        f"experiment={summary.experiment_id} status={summary.status} "
        f"cases={summary.cases} recall_at_5={summary.recall_at_5} "
        f"precision_at_5={summary.precision_at_5} mrr={summary.mrr} "
        f"unauthorized_context_count={summary.unauthorized_context_count} "
        f"output={output}"
    )
    return 1 if _hard_failure(summary) else 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment", default="E3")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--health-s3-key")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
