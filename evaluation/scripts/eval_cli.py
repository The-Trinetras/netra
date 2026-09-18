"""Command line for the offline evaluation workflow.

Run from the repository root (these modules import each other by name):

    python evaluation/scripts/eval_cli.py <command> [options]

Local, no network:
  validate         dataset summary, snapshot id, reference-status counts
  freeze-heldout   freeze the held-out split (refused unless all gold)
  init-run         create an immutable run manifest in an artifacts dir
  replay-fixtures  freeze a dataset's candidate_fixture texts as outputs
                   (scorer exercise only; never reported as Netra output)
  status           per-outcome counts and pending units for a run
  calibrate        agreement report against human labels (not held-out)
  compare          paired baseline/candidate report (JSON + Markdown)

External, refused without --live and an execution authorization:
  judge            score pending units on the authenticated Modal endpoint
  upload           upload persisted dataset/results to AX (SDK pending)

Nothing here reads secrets except `judge --live`, which reads the proxy
token from the environment variables named in judge_client.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from ax_upload import PendingAxSdkClient, upload_run
from calibration import HumanLabel, calibrate
from comparison import DeterministicFinding, compare_runs, render_markdown
from eval_dataset import freeze_heldout, load_dataset
from eval_store import JudgeConfig, ProducerConfig, RunManifest, RunStore
from judge_client import ENV_JUDGE_URL, ENV_MODAL_TOKEN_ID, ENV_MODAL_TOKEN_SECRET, HttpxJudgeTransport, RunAllowance
from judge_runner import judge_run, load_rubrics

REPO = Path(__file__).resolve().parents[2]
RUBRICS = REPO / "evaluation" / "rubrics"
LOCKED = REPO / "evaluation" / "locked"


def _print(value) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, default=str))


def cmd_validate(args) -> int:
    snapshot = load_dataset(Path(args.dataset))
    by_split: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for case in snapshot.cases:
        by_split[case.split] = by_split.get(case.split, 0) + 1
        status = case.reference.status.value if case.reference else "missing"
        by_status[status] = by_status.get(status, 0) + 1
    _print({"snapshot_id": snapshot.snapshot_id, "content_hash": snapshot.content_hash,
            "cases": len(snapshot.cases), "by_split": by_split, "reference_status": by_status})
    return 0


def cmd_freeze(args) -> int:
    frozen = freeze_heldout(load_dataset(Path(args.dataset)), args.by, LOCKED)
    _print(frozen.model_dump(mode="json"))
    return 0


def cmd_init_run(args) -> int:
    snapshot = load_dataset(Path(args.dataset))
    criteria = args.criteria.split(",")
    rubrics = load_rubrics(RUBRICS, criteria)
    cases = [case.case_id for case in snapshot.by_split(args.split)]
    if not cases:
        print(f"no {args.split} cases in {snapshot.snapshot_id}", file=sys.stderr)
        return 2
    manifest = RunManifest(
        run_id=args.run_id,
        dataset_name=snapshot.dataset_name,
        dataset_hash=snapshot.content_hash,
        split=args.split,
        case_ids=cases,
        repetitions=args.repetitions,
        criteria=criteria,
        rubric_hashes={k: v[1] for k, v in rubrics.items()},
        producer=ProducerConfig.model_validate_json(Path(args.producer).read_text(encoding="utf-8")),
        judge=JudgeConfig.model_validate_json(Path(args.judge_config).read_text(encoding="utf-8")),
        created_at=datetime.now(timezone.utc),
    )
    RunStore(Path(args.artifacts), args.run_id).create(manifest)
    _print({"run_id": manifest.run_id, "judge_config_id": manifest.judge.judge_config_id,
            "cases": len(cases), "criteria": criteria})
    return 0


def cmd_replay_fixtures(args) -> int:
    store = RunStore(Path(args.artifacts), args.run_id)
    manifest = store.manifest()
    if manifest.producer.source != "fixture_replay":
        print("replay-fixtures only fills runs whose producer.source is fixture_replay", file=sys.stderr)
        return 2
    snapshot = load_dataset(Path(args.dataset))
    written = 0
    for case_id in manifest.case_ids:
        text = snapshot.case(case_id).candidate_fixture
        if text is None:
            continue
        for repetition in range(manifest.repetitions):
            store.append_output(case_id, repetition, text)
            written += 1
    _print({"frozen_outputs": written})
    return 0


def cmd_status(args) -> int:
    store = RunStore(Path(args.artifacts), args.run_id)
    manifest = store.manifest()
    results = store.effective_results()
    counts: dict[str, int] = {}
    for result in results.values():
        label = result.outcome.value + (f":{result.error_code.value}" if result.error_code else "")
        counts[label] = counts.get(label, 0) + 1
    expected = len(manifest.case_ids) * manifest.repetitions * len(manifest.criteria)
    _print({"run_id": manifest.run_id, "expected_units": expected, "recorded_units": len(results),
            "pending_units": expected - len(results), "outcomes": counts,
            "frozen_outputs": len(store.outputs()), "uploads": {k: v.state for k, v in store.upload_states().items()}})
    return 0


def cmd_calibrate(args) -> int:
    labels = [HumanLabel.model_validate(item) for item in json.loads(Path(args.labels).read_text(encoding="utf-8"))]
    report = calibrate(RunStore(Path(args.artifacts), args.run_id), labels)
    _print({cid: {"labelled": c.labelled, "compared": c.compared, "below_floor": c.below_floor,
                  "exact_rate": c.exact_rate, "within_one_rate": c.within_one_rate,
                  "mean_abs_error": c.mean_abs_error, "judge_not_scored": c.judge_not_scored,
                  "confusion": c.confusion, "false_high": c.false_high, "disagreements": c.disagreements}
            for cid, c in report.items()})
    return 0


def cmd_compare(args) -> int:
    snapshot = load_dataset(Path(args.dataset))
    deterministic = [DeterministicFinding.model_validate(item) for item in
                     json.loads(Path(args.deterministic).read_text(encoding="utf-8"))] if args.deterministic else []
    traces = json.loads(Path(args.trace_status).read_text(encoding="utf-8")) if args.trace_status else None
    report = compare_runs(RunStore(Path(args.artifacts), args.baseline), RunStore(Path(args.artifacts), args.candidate),
                          snapshot, LOCKED, deterministic, traces)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{report.comparison_id}.md").write_text(render_markdown(report), encoding="utf-8")
    (out / f"{report.comparison_id}.json").write_text(json.dumps({
        "comparison_id": report.comparison_id, "verdict": report.verdict,
        "per_criterion": report.per_criterion(),
        "rows": [row.__dict__ for row in report.rows],
        "critical_failures": [f.model_dump() for f in report.critical_failures],
        "trace_status": report.trace_status,
    }, indent=2, default=str), encoding="utf-8")
    _print({"comparison_id": report.comparison_id, "verdict": report.verdict})
    return 0


def cmd_judge(args) -> int:
    if not args.live:
        print("judge calls the external Modal scorer; rerun with --live only under an explicit "
              "execution authorization (credits, allowance, deployment).", file=sys.stderr)
        return 2
    missing = [name for name in (ENV_JUDGE_URL, ENV_MODAL_TOKEN_ID, ENV_MODAL_TOKEN_SECRET) if not os.environ.get(name)]
    if missing:
        print("missing environment variables: " + ", ".join(missing), file=sys.stderr)
        return 2
    store = RunStore(Path(args.artifacts), args.run_id)
    manifest = store.manifest()
    snapshot = load_dataset(Path(args.dataset))
    transport = HttpxJudgeTransport(os.environ[ENV_JUDGE_URL], os.environ[ENV_MODAL_TOKEN_ID],
                                    os.environ[ENV_MODAL_TOKEN_SECRET], timeout_seconds=args.timeout)
    allowance = RunAllowance(max_gpu_seconds=args.max_gpu_seconds, margin_seconds=args.margin_seconds,
                             per_call_estimate_seconds=args.per_call_seconds)

    async def _go():
        try:
            return await judge_run(store, snapshot, load_rubrics(RUBRICS, manifest.criteria), transport, allowance)
        finally:
            await transport.aclose()

    summary = asyncio.run(_go())
    _print({**summary.__dict__, "allowance_spent_seconds": allowance.spent_seconds,
            "estimated_gpu_cost_usd": allowance.estimated_cost_usd(),
            "reminder": "read billed usage from the Modal account and stop the app explicitly"})
    return 0 if summary.stopped_reason is None else 3


def cmd_upload(args) -> int:
    if not args.live:
        print("upload sends data to Arize AX; rerun with --live only under an explicit authorization.",
              file=sys.stderr)
        return 2
    summary = upload_run(RunStore(Path(args.artifacts), args.run_id), load_dataset(Path(args.dataset)),
                         PendingAxSdkClient())
    _print(summary.__dict__)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="eval_cli")
    sub = parser.add_subparsers(dest="command", required=True)

    def run_args(p, dataset=True):
        p.add_argument("--artifacts", required=True, help="artifacts root, outside the repository")
        p.add_argument("--run-id", required=True)
        if dataset:
            p.add_argument("--dataset", required=True)

    p = sub.add_parser("validate"); p.add_argument("dataset"); p.set_defaults(func=cmd_validate)
    p = sub.add_parser("freeze-heldout"); p.add_argument("dataset"); p.add_argument("--by", required=True); p.set_defaults(func=cmd_freeze)
    p = sub.add_parser("init-run"); run_args(p)
    p.add_argument("--split", choices=["development", "calibration", "heldout"], required=True)
    p.add_argument("--criteria", required=True, help="comma-separated evaluation ids")
    p.add_argument("--repetitions", type=int, default=1)
    p.add_argument("--producer", required=True, help="ProducerConfig JSON file")
    p.add_argument("--judge-config", required=True, help="JudgeConfig JSON file")
    p.set_defaults(func=cmd_init_run)
    p = sub.add_parser("replay-fixtures"); run_args(p); p.set_defaults(func=cmd_replay_fixtures)
    p = sub.add_parser("status"); run_args(p, dataset=False); p.set_defaults(func=cmd_status)
    p = sub.add_parser("calibrate"); run_args(p, dataset=False); p.add_argument("--labels", required=True); p.set_defaults(func=cmd_calibrate)
    p = sub.add_parser("compare")
    p.add_argument("--artifacts", required=True); p.add_argument("--dataset", required=True)
    p.add_argument("--baseline", required=True); p.add_argument("--candidate", required=True)
    p.add_argument("--deterministic"); p.add_argument("--trace-status"); p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_compare)
    p = sub.add_parser("judge"); run_args(p); p.add_argument("--live", action="store_true")
    p.add_argument("--max-gpu-seconds", type=float, required=True)
    p.add_argument("--margin-seconds", type=float, default=600.0)
    p.add_argument("--per-call-seconds", type=float, default=30.0)
    p.add_argument("--timeout", type=float, default=120.0)
    p.set_defaults(func=cmd_judge)
    p = sub.add_parser("upload"); run_args(p); p.add_argument("--live", action="store_true"); p.set_defaults(func=cmd_upload)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
