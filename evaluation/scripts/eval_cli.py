"""Command line for the offline evaluation workflow.

Run from the repository root (these modules import each other by name):

    python evaluation/scripts/eval_cli.py <command> [options]

Local, no network:
  validate         dataset summary, snapshot id, reference-status counts
  check            grounding + dataset rules (exit 1 on any error)
  freeze-heldout   freeze the held-out split (refused unless all gold)
  init-run         create an immutable run manifest in an artifacts dir
                   (held-out runs refused unless the freeze verifies)
  producer-inputs  export what Netra may see for a split (no references)
  import-outputs   freeze producer outputs (JSONL) into a run
  replay-fixtures  freeze a dataset's candidate_fixture texts as outputs
                   (scorer exercise only; never reported as Netra output)
  assert           deterministic per-case assertions over a run's outputs
  status           per-outcome counts and pending units for a run
  calibrate        agreement report against human labels (not held-out)
  compare          paired baseline/candidate report (JSON + Markdown)
  review-package   write the review worksheet and blank review template
  apply-review     apply a completed review template as a new dataset
  export-ax        write the AX dataset rows for a split to a local file
  plan-experiment  fix a before/after plan before results exist
  check-plan       verify a run matches its arm of a plan

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

from ax_upload import PendingAxSdkClient, export_dataset_rows, upload_run
from calibration import HumanLabel, calibrate
from case_assertions import evaluate_run, findings, summarize
from comparison import DeterministicFinding, compare_runs, render_markdown
from dataset_checks import check_dataset
from eval_dataset import DatasetFile, HeldoutChangedError, freeze_heldout, load_dataset, verify_frozen_heldout
from eval_store import JudgeConfig, ProducerConfig, RunManifest, RunStore
from experiment_plan import ArmPlan, check_run, load_plan, make_plan, manifest_for, write_plan
from grounding import Registry, check_cases
from judge_client import ENV_JUDGE_URL, ENV_MODAL_TOKEN_ID, ENV_MODAL_TOKEN_SECRET, HttpxJudgeTransport, RunAllowance
from judge_runner import judge_run, load_rubrics
from producer_io import import_outputs, write_producer_inputs
from review_sheet import ReviewFile, apply_review, write_review_package

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


def cmd_check(args) -> int:
    snapshot = load_dataset(Path(args.dataset))
    issues = check_cases(snapshot.cases, Registry.load()) + check_dataset(snapshot)
    errors = [i for i in issues if i.severity == "error"]
    _print({"snapshot_id": snapshot.snapshot_id, "cases": len(snapshot.cases), "errors": len(errors),
            "warnings": len(issues) - len(errors),
            "issues": [{"severity": i.severity, "code": i.code, "case_id": i.case_id, "detail": i.detail} for i in issues]})
    return 1 if errors else 0


def _heldout_guard(snapshot, split: str) -> str | None:
    """Held-out cases may be run only after their freeze verifies: running
    unfrozen held-out cases would let them inform tuning unrecorded."""

    if split != "heldout":
        return None
    try:
        verify_frozen_heldout(snapshot, LOCKED)
    except HeldoutChangedError as error:
        return f"refused: {error}. Held-out cases must be frozen (all gold) before any run."
    return None


def cmd_init_run(args) -> int:
    snapshot = load_dataset(Path(args.dataset))
    if args.plan:
        plan = load_plan(Path(args.plan))
        if plan.dataset_hash != snapshot.content_hash:
            print("the plan was made for a different dataset snapshot", file=sys.stderr)
            return 2
        refusal = _heldout_guard(snapshot, plan.split)
        if refusal:
            print(refusal, file=sys.stderr)
            return 2
        manifest = manifest_for(plan, args.arm)
        RunStore(Path(args.artifacts), manifest.run_id).create(manifest)
        _print({"run_id": manifest.run_id, "plan_id": plan.plan_id, "arm": args.arm,
                "judge_config_id": manifest.judge.judge_config_id, "cases": len(manifest.case_ids)})
        return 0
    if not (args.run_id and args.split and args.criteria and args.producer and args.judge_config):
        print("init-run needs --plan/--arm, or --run-id, --split, --criteria, --producer and --judge-config",
              file=sys.stderr)
        return 2
    refusal = _heldout_guard(snapshot, args.split)
    if refusal:
        print(refusal, file=sys.stderr)
        return 2
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
                             per_call_estimate_seconds=args.per_call_seconds,
                             per_cold_start_estimate_seconds=args.cold_start_seconds)

    async def _go():
        try:
            return await judge_run(store, snapshot, load_rubrics(RUBRICS, manifest.criteria), transport, allowance)
        finally:
            await transport.aclose()

    summary = asyncio.run(_go())
    _print({**summary.__dict__, "allowance_spent_seconds": allowance.spent_seconds,
            "cold_starts": allowance.cold_starts,
            "cold_start_seconds": round(allowance.cold_start_seconds, 3),
            "estimated_gpu_cost_usd": allowance.estimated_cost_usd(),
            "reminder": "read billed usage from the Modal account and stop the app explicitly"})
    return 0 if summary.stopped_reason is None else 3


def cmd_producer_inputs(args) -> int:
    snapshot = load_dataset(Path(args.dataset))
    _print(write_producer_inputs(snapshot, args.split, Path(args.out)))
    return 0


def cmd_import_outputs(args) -> int:
    store = RunStore(Path(args.artifacts), args.run_id)
    _print({"run_id": args.run_id, "producer": store.manifest().producer.source,
            **import_outputs(store, Path(args.outputs))})
    return 0


def cmd_assert(args) -> int:
    store = RunStore(Path(args.artifacts), args.run_id)
    results = evaluate_run(store, load_dataset(Path(args.dataset)))
    summary = summarize(results)
    if args.findings_out:
        out = Path(args.findings_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps([f.model_dump(mode="json") for f in findings(results, args.arm)], indent=2) + "\n",
                       encoding="utf-8")
        summary["findings_file"] = str(out)
    _print({"run_id": args.run_id, "producer": store.manifest().producer.source, **summary})
    return 1 if summary["critical_failures"] else 0


def cmd_review_package(args) -> int:
    snapshot = load_dataset(Path(args.dataset))
    _print(write_review_package(snapshot, Registry.load(), Path(args.out)))
    return 0


def cmd_apply_review(args) -> int:
    path = Path(args.dataset)
    dataset = DatasetFile.model_validate(json.loads(path.read_text(encoding="utf-8")))
    review = ReviewFile.model_validate(json.loads(Path(args.review).read_text(encoding="utf-8")))
    reviewed = apply_review(dataset, load_dataset(path), review, args.new_name)
    out = Path(args.out)
    if out.exists():
        print(f"{out} exists; a reviewed dataset is never overwritten", file=sys.stderr)
        return 2
    out.write_text(json.dumps(reviewed.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    snapshot = load_dataset(out)
    gold = sum(1 for case in snapshot.cases if case.reference is not None and case.reference.is_gold)
    _print({"written": str(out), "snapshot_id": snapshot.snapshot_id, "gold_references": gold})
    return 0


def cmd_export_ax(args) -> int:
    _print(export_dataset_rows(load_dataset(Path(args.dataset)), args.split, Path(args.out)))
    return 0


def cmd_plan_experiment(args) -> int:
    snapshot = load_dataset(Path(args.dataset))
    criteria = args.criteria.split(",")
    rubrics = load_rubrics(RUBRICS, criteria)
    judge = JudgeConfig.model_validate_json(Path(args.judge_config).read_text(encoding="utf-8"))

    def arm(run_id, producer_path):
        return ArmPlan(run_id=run_id,
                       producer=ProducerConfig.model_validate_json(Path(producer_path).read_text(encoding="utf-8")))

    acceptance = json.loads(Path(args.acceptance).read_text(encoding="utf-8")) if args.acceptance else []
    plan = make_plan(snapshot, args.split, criteria, {k: v[1] for k, v in rubrics.items()}, judge,
                     arm(args.baseline_run, args.baseline_producer), arm(args.candidate_run, args.candidate_producer),
                     repetitions=args.repetitions, acceptance_criteria=acceptance, notes=args.notes)
    plan_id = write_plan(plan, Path(args.out))
    _print({"plan_id": plan_id, "cases": len(plan.case_ids), "split": plan.split,
            "judge_config_id": plan.judge.judge_config_id, "acceptance_criteria_declared": len(plan.acceptance_criteria)})
    return 0


def cmd_check_plan(args) -> int:
    plan = load_plan(Path(args.plan))
    problems = check_run(plan, args.arm, RunStore(Path(args.artifacts), args.run_id).manifest())
    _print({"plan_id": plan.plan_id, "arm": args.arm, "run_id": args.run_id, "matches": not problems,
            "problems": problems})
    return 0 if not problems else 1


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
    p = sub.add_parser("check"); p.add_argument("dataset"); p.set_defaults(func=cmd_check)
    p = sub.add_parser("freeze-heldout"); p.add_argument("dataset"); p.add_argument("--by", required=True); p.set_defaults(func=cmd_freeze)
    p = sub.add_parser("init-run")
    p.add_argument("--artifacts", required=True, help="artifacts root, outside the repository")
    p.add_argument("--dataset", required=True)
    p.add_argument("--run-id")
    p.add_argument("--plan", help="experiment plan JSON (takes run id, split, criteria, producer and judge from it)")
    p.add_argument("--arm", choices=["baseline", "candidate"], default="baseline")
    p.add_argument("--split", choices=["development", "calibration", "heldout"])
    p.add_argument("--criteria", help="comma-separated evaluation ids")
    p.add_argument("--repetitions", type=int, default=1)
    p.add_argument("--producer", help="ProducerConfig JSON file")
    p.add_argument("--judge-config", help="JudgeConfig JSON file")
    p.set_defaults(func=cmd_init_run)
    p = sub.add_parser("producer-inputs"); p.add_argument("--dataset", required=True)
    p.add_argument("--split", choices=["development", "calibration", "heldout"], required=True)
    p.add_argument("--out", required=True); p.set_defaults(func=cmd_producer_inputs)
    p = sub.add_parser("import-outputs"); run_args(p, dataset=False); p.add_argument("--outputs", required=True)
    p.set_defaults(func=cmd_import_outputs)
    p = sub.add_parser("assert"); run_args(p); p.add_argument("--arm", choices=["baseline", "candidate"], default="baseline")
    p.add_argument("--findings-out", help="write DeterministicFinding JSON for compare --deterministic")
    p.set_defaults(func=cmd_assert)
    p = sub.add_parser("review-package"); p.add_argument("--dataset", required=True); p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_review_package)
    p = sub.add_parser("apply-review"); p.add_argument("--dataset", required=True); p.add_argument("--review", required=True)
    p.add_argument("--new-name", required=True); p.add_argument("--out", required=True); p.set_defaults(func=cmd_apply_review)
    p = sub.add_parser("export-ax"); p.add_argument("--dataset", required=True)
    p.add_argument("--split", choices=["development", "calibration", "heldout"], required=True)
    p.add_argument("--out", required=True); p.set_defaults(func=cmd_export_ax)
    p = sub.add_parser("plan-experiment"); p.add_argument("--dataset", required=True)
    p.add_argument("--split", choices=["development", "calibration", "heldout"], required=True)
    p.add_argument("--criteria", required=True); p.add_argument("--judge-config", required=True)
    p.add_argument("--baseline-run", required=True); p.add_argument("--baseline-producer", required=True)
    p.add_argument("--candidate-run", required=True); p.add_argument("--candidate-producer", required=True)
    p.add_argument("--repetitions", type=int, default=1)
    p.add_argument("--acceptance", help="JSON list of human-written acceptance criteria, declared before results")
    p.add_argument("--notes"); p.add_argument("--out", required=True); p.set_defaults(func=cmd_plan_experiment)
    p = sub.add_parser("check-plan"); p.add_argument("--plan", required=True); p.add_argument("--artifacts", required=True)
    p.add_argument("--run-id", required=True); p.add_argument("--arm", choices=["baseline", "candidate"], required=True)
    p.set_defaults(func=cmd_check_plan)
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
    # OPT-8: reserved on every dispatch because min_containers=0 means the
    # container can always have gone away. Over-reserving stops the run early;
    # under-reserving overruns the cap, so the default leans high. Replace it
    # with a measured value once a real deployment reports cold_start_seconds.
    p.add_argument("--cold-start-seconds", type=float, default=120.0)
    p.add_argument("--timeout", type=float, default=120.0)
    p.set_defaults(func=cmd_judge)
    p = sub.add_parser("upload"); run_args(p); p.add_argument("--live", action="store_true"); p.set_defaults(func=cmd_upload)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
