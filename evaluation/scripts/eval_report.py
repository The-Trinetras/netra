"""Readable local report for a grounded dataset (coverage, checks, review, gaps).

Everything in the report is computed from committed artifacts at the time
it is generated: dataset, source registry, rubrics, deterministic checks and
a LABELLED plumbing run of the deterministic assertions over fixture
candidates (plumbing_fixtures.py). It contains no judge scores: none exist
until an authorized Modal run over real Netra outputs, and scripted scores
are never reported.

Usage (from the repository root):
    python evaluation/scripts/eval_report.py [--dataset ...] [--out ...]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from case_assertions import evaluate_run, summarize
from dataset_checks import CATEGORIES, check_dataset
from eval_dataset import load_dataset
from eval_store import JudgeConfig, ProducerConfig, RunManifest, RunStore
from grounding import Registry, check_cases
from judge_runner import load_rubrics
from producer_io import import_outputs
from review_sheet import FIRST_BATCH, MINIMUM_BATCH

REPO = Path(__file__).resolve().parents[2]
DEFAULT_DATASET = REPO / "evaluation" / "datasets" / "netra_grounded_v1.json"
DEFAULT_OUT = REPO / "evaluation" / "review" / "netra_grounded_v1" / "report.md"
CRITERIA = ["factual_correctness_v2", "source_support_v2", "citation_correctness_v1", "teaching_usefulness_v2",
            "appropriate_uncertainty_v1"]
PLUMBING_JUDGE = JudgeConfig(model_id="prometheus-eval/prometheus-7b-v2.0",
                             model_revision="66ffb1fc20beebfb60a3964a957d9011723116c5",
                             tokenizer_revision="66ffb1fc20beebfb60a3964a957d9011723116c5",
                             template_version="prometheus2-mistral-v1", host="fixture", gpu="none", dtype="bfloat16",
                             image_digest="fixture", inference_library="fixture", max_total_tokens=4096,
                             max_new_tokens=512, temperature=0.0, seed=0)


def _commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True,
                              check=True).stdout.strip()
    except Exception:  # pragma: no cover - report still renders without git
        return "unknown"


def _plumbing(snapshot) -> dict[str, dict]:
    import plumbing_fixtures

    rubrics = load_rubrics(REPO / "evaluation" / "rubrics", CRITERIA)
    out = {}
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for arm, outputs in (("baseline", plumbing_fixtures.BASELINE), ("candidate", plumbing_fixtures.CANDIDATE)):
            store = RunStore(root, f"plumbing-{arm}")
            store.create(RunManifest(
                run_id=f"plumbing-{arm}", dataset_name=snapshot.dataset_name, dataset_hash=snapshot.content_hash,
                split="calibration", case_ids=[c.case_id for c in snapshot.by_split("calibration")], repetitions=1,
                criteria=CRITERIA, rubric_hashes={k: v[1] for k, v in rubrics.items()},
                producer=ProducerConfig(label=f"fixture-{arm}", source="fixture_replay", commit=_commit()),
                judge=PLUMBING_JUDGE, created_at=datetime.now(timezone.utc)))
            path = root / f"{arm}.jsonl"
            path.write_text("".join(json.dumps({"case_id": k, **v}) + "\n" for k, v in outputs.items()), encoding="utf-8")
            import_outputs(store, path)
            out[arm] = summarize(evaluate_run(store, snapshot))
    return out


def render(dataset_path: Path) -> str:
    snapshot = load_dataset(dataset_path)
    registry = Registry.load()
    issues = check_cases(snapshot.cases, registry) + check_dataset(snapshot)
    errors = [i for i in issues if i.severity == "error"]
    cases = snapshot.cases
    splits = ["development", "calibration", "heldout"]
    rel = dataset_path.relative_to(REPO).as_posix()

    lines = [
        f"# Evaluation package report: `{snapshot.snapshot_id}`",
        "",
        f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} from commit `{_commit()[:12]}` by "
        "`evaluation/scripts/eval_report.py`. Dataset `" + rel + "`, content hash `" + snapshot.content_hash + "`.",
        "",
        "**Status: DRAFT.** Every reference is an unreviewed suggestion; splits are proposed candidate groups and "
        "nothing is frozen; no judge has scored anything; no calibration exists. All sources are synthetic. "
        "Nothing here is evidence about Netra's quality or about student learning.",
        "",
        "## Counts",
        "",
        f"- Cases: **{len(cases)}**; by proposed split: "
        + ", ".join(f"{s} {len(snapshot.by_split(s))}" for s in splits),
        "- By provenance: " + ", ".join(f"{k} {v}" for k, v in sorted(Counter(c.provenance.origin for c in cases if c.provenance).items())),
        "- By review status: " + ", ".join(f"{k} {v}" for k, v in sorted(Counter(c.review_status for c in cases).items())),
        "- By reference status: " + ", ".join(f"{k} {v}" for k, v in sorted(Counter(
            c.reference.status.value if c.reference else "missing (deliberate)" for c in cases).items())),
        f"- With prior exposure (informed implementation or tests; never held-out): "
        f"{sum(1 for c in cases if c.prior_exposure)}; without: {sum(1 for c in cases if not c.prior_exposure)}",
        f"- Deterministic assertions: {sum(len(c.assertions) for c in cases)} "
        f"({sum(1 for c in cases for a in c.assertions if a.critical)} critical); calculations recomputed: "
        f"{sum(len(c.calculations) for c in cases)}; excerpts verified verbatim: {sum(len(c.source_excerpts) for c in cases)}; "
        f"withheld items verified: {sum(len(c.withheld_evidence) for c in cases)}",
        "",
        "## Coverage (category × proposed split)",
        "",
        "| category | " + " | ".join(splits) + " | total |",
        "|---|" + "---|" * (len(splits) + 1),
    ]
    for category, description in CATEGORIES.items():
        counts = [sum(1 for c in snapshot.by_split(s) if c.category == category) for s in splits]
        lines.append(f"| {category} ({description}) | " + " | ".join(str(n) for n in counts) + f" | {sum(counts)} |")
    behaviours = Counter(c.expected_behavior for c in cases)
    lines += ["", "Expected behaviours: " + ", ".join(f"{k} {v}" for k, v in sorted(behaviours.items())),
              f"Abstain / state-limitation / clarify / surface-contradiction cases: "
              f"{sum(behaviours[k] for k in ('abstain', 'state_limitation', 'clarify', 'surface_contradiction'))} "
              f"of {len(cases)} (answer-giving cases dominate, so refusing more cannot look like improvement).", ""]

    lines += ["## Validation results (executed)", "",
              f"`eval_cli.py check` → **{len(errors)} errors, {len(issues) - len(errors)} warning(s)**.", ""]
    lines += [f"- {i.severity} `{i.code}` {i.case_id}: {i.detail}" for i in issues] or ["- none"]
    lines += ["", "What the checks establish: every excerpt equals the registered fixture text (id, version, variant, "
              "locator, trust, time range); supplied evidence is usable for the student's account and pinned version; "
              "every withheld item really is denied / deleted / stale / failed / unpinned per the registry; walkthrough "
              "quotes are verbatim; every calculation recomputes exactly (fractions + unit algebra) from inputs stated "
              "in the cited evidence or the student's words; every reference passes its own text assertions; families "
              "do not span splits; exposed cases are not held-out; the judge instruction contains the actual input and "
              "turns; the judge prompt fits the 4,096-token budget by a conservative character estimate (the pinned "
              "tokenizer must confirm before a live run). What they cannot establish: that references are pedagogically "
              "good, that fixture renderings match an original page, or anything about real Netra outputs.", ""]

    lines += ["## Sources", "", "| source | origin | versions | evidence items | notes |", "|---|---|---|---|---|"]
    for source in registry.data["sources"]:
        versions = ", ".join(f"v{v['version_number']} {v['status']}{' active' if v['is_active'] else ''}"
                             f"{' deleted' if v['deleted'] else ''}" for v in source["versions"])
        notes = "; ".join(source.get("notes", [])[:2]).replace("|", "/")
        excluded = source.get("excluded") or []
        if excluded:
            notes += f"; EXCLUDED {', '.join(x['evidence_id'] for x in excluded)} ({excluded[0]['reason']})"
        lines.append(f"| `{source['source_key']}` | {source['origin']} | {versions} | {len(source['evidence'])} | {notes} |")
    lines += ["", "Fixture inconsistencies found while building the registry (reported, not silently merged):", "",
              "- M1 and M3 render the same AgentSpec 'Ohm's Law Study Pack' with different source version ids and page "
              "locators (M1: `tbl01` page 3, `fig02` page 2; M3: `p4/tbl01`, `p4/fig02`). They are separate sources here "
              "and no case mixes them.",
              "- M2 and M4 render chapter 4 objects under one version id with different wording (e.g. table 4.1) and "
              "different locator schemes; a case uses one rendering per object.",
              "- M1's `ev-fig02` text is a placeholder ('Figure 2 description'); it is excluded.",
              "- The real-lecture golden files (`netra_e3_real_golden_v1.jsonl`, `netra_p3_answer_golden_v1.jsonl`, "
              "16 rows) are NOT used: their PDF is absent locally, so no supporting location can be checked, and "
              "redistribution rights are unconfirmed.", ""]

    lines += ["## Rubrics", "", "| evaluation id | status | hash | supersedes |", "|---|---|---|---|"]
    for evaluation_id, (rubric, rubric_hash) in load_rubrics(REPO / "evaluation" / "rubrics", CRITERIA).items():
        lines.append(f"| `{evaluation_id}` | {rubric.status} | `{rubric_hash[:12]}` | {rubric.supersedes or '-'} |")
    lines += ["", "Each rubric has a scope note (what it does not judge) and one concrete anchor example per score, "
              "drawn only from development-family material; examples are for reviewers and are not sent to the judge. "
              "v1 rubrics are unchanged (tutor-reference-v1 still uses them).", ""]

    plumbing = _plumbing(snapshot)
    lines += ["## Deterministic assertions: plumbing demonstration (LABELLED FIXTURES, not Netra output)", "",
              "Hand-written fixture candidates (`evaluation/scripts/plumbing_fixtures.py`) run through the real import "
              "and assertion code on the calibration split. The candidate arm contains deliberate failures to show "
              "they are caught. These numbers describe the tooling, not Netra.", "",
              "| arm | assertions | passed | failed | not evaluable | missing output | critical failures |",
              "|---|---|---|---|---|---|---|"]
    for arm, summary in plumbing.items():
        by = summary["by_outcome"]
        lines.append(f"| {arm} | {summary['assertions']} | {by.get('passed', 0)} | {by.get('failed', 0)} | "
                     f"{by.get('not_evaluable', 0)} | {by.get('missing_output', 0)} | {len(summary['critical_failures'])} |")
    lines += ["", "Candidate-arm failures caught:", ""]
    lines += [f"- critical: {f}" for f in plumbing["candidate"]["critical_failures"]]
    lines += [f"- {f}" for f in plumbing["candidate"]["failures"]]
    lines += ["", "Judge plumbing (scripted transport) is exercised by `test_grounded_workflow.py`: invalid output → "
              "`invalid`, a failed call → retried on resume only, finished units skipped, paired comparison blocked "
              "by critical deterministic failures. No scripted score appears in this report.", ""]

    lines += ["## Review priorities", "",
              f"**Smallest batch that unlocks meaningful scoring: {len(MINIMUM_BATCH)} calibration cases** "
              "(the plan's floor is ten human-labelled cases per criterion; all five criteria apply to each):", ""]
    for case_id in MINIMUM_BATCH:
        case = snapshot.case(case_id)
        lines.append(f"1. `{case_id}` ({case.category}; expected `{case.expected_behavior}`)")
    lines += ["", f"Rest of the first batch ({len(FIRST_BATCH) - len(MINIMUM_BATCH)}):", ""]
    lines += [f"- `{case_id}` ({snapshot.case(case_id).category})" for case_id in FIRST_BATCH[len(MINIMUM_BATCH):]]
    lines += ["", "Meaningful scoring then still needs, in order: (1) real Netra outputs for those cases "
              "(`producer-inputs` → Netra run with live Gemini/Groq keys → `import-outputs`); (2) an authorized Modal "
              "judge run; (3) the same humans scoring the same outputs per criterion (`calibrate`, labels file); "
              "(4) only then any aggregate. Held-out candidates need gold references and a freeze before any run "
              "(`init-run` refuses otherwise).", ""]

    lines += ["## Exposure record (cases that informed implementation)", ""]
    exposed = Counter(c.problem_family for c in cases if c.prior_exposure)
    lines += [f"- `{family}`: {n} case(s) — never eligible as untouched held-out" for family, n in sorted(exposed.items())]
    lines += ["- The held-out candidates (`mini-*` families) were written on 2026-09-19 and have informed no "
              "implementation, prompt or rubric (rubric examples avoid them). If one ever informs a fix, record that "
              "in its `prior_exposure` and replace it with a fresh case.", ""]

    lines += ["## AX dataset mapping (prepared, not uploaded)", "",
              "`eval_cli.py export-ax --dataset <dataset> --split <split> --out <file>` writes exactly the rows an upload "
              "would send. The AX SDK client is still `PendingAxSdkClient` (fails closed); **AX compatibility has not "
              "been verified live.**", "",
              "| AX column | from | notes |", "|---|---|---|",
              "| `row_key`, `case_id` | case id | stable key for reconciliation |",
              "| `split`, `kind`, `category`, `problem_family`, `expected_behavior` | case | proposed split until frozen |",
              "| `instruction`, `student_input`, `conversation` | case | conversation as JSON |",
              "| `evidence`, `evidence_ids`, `source_version_ids` | supplied excerpts | verbatim fixture text |",
              "| `withheld_evidence` | withheld items | ids and reasons only; text never exported |",
              "| `reference`, `reference_status`, `reference_author`, `reference_reviewer`, `reference_rationale`, `acceptable_alternatives` | reference | suggested until reviewed |",
              "| `criteria`, `assertions`, `failure_modes` | case | assertions as JSON |",
              "| `provenance_origin`, `prior_exposure`, `review_status`, `permission`, `dataset_hash` | case | rejected cases are refused |",
              "", "Experiment rows (per run) keep every criterion's outcome, score, error and feedback explicit "
              "(`ax_upload.experiment_rows`).", ""]

    lines += ["## Remaining gaps", "",
              "- **No human review**: 0 gold references; calibration impossible until the minimum batch is reviewed and "
              "outputs are labelled.",
              "- **No real Netra outputs**: producing them needs live Gemini/Groq keys (`NETRA_GEMINI_API_KEY`, "
              "`NETRA_GROQ_API_KEY`) and a seeded local database; not done.",
              "- **All sources synthetic**: the only representative material (the Ohm's-law scenario) is exposed; held-out "
              "candidates are self-authored miniatures. A permitted real source (e.g. the lecture PDF once rights are "
              "confirmed and the file is available) is needed for representative held-out cases.",
              "- **No original-media review**: M3 renderings are of structured fixtures; text judging cannot validate "
              "pixels, audio or NVDA behaviour.",
              "- **Token sizes are estimates** (characters / 3); confirm with the pinned tokenizer before a live run.",
              "- **Protocol behaviour** (STOP, reconnect, duplicate commits, replay) is deliberately not in this dataset; "
              "it is covered by deterministic integration tests (e.g. `api/tests/server/`, `client/tests/.../LiveServerTests.cs`).",
              "- Thin categories: judge_robustness and assessment_integrity are development-only; held-out has one "
              "embedded-instruction case.", ""]

    lines += ["## Next commands (from the repository root)", "", "```text",
              f"python evaluation/scripts/eval_cli.py check {rel}",
              f"python evaluation/scripts/eval_cli.py review-package --dataset {rel} --out evaluation/review/netra_grounded_v1",
              f"python evaluation/scripts/eval_cli.py apply-review --dataset {rel} --review <filled review_template.json> --new-name netra-grounded-v1r1 --out evaluation/datasets/netra_grounded_v1r1.json",
              f"python evaluation/scripts/eval_cli.py producer-inputs --dataset <reviewed dataset> --split calibration --out <artifacts>/inputs.jsonl",
              "# run Netra over inputs.jsonl (live keys; outside this tool), writing {case_id, response, cited_evidence_ids, structured, trace_id} lines",
              "python evaluation/scripts/eval_cli.py plan-experiment --dataset <reviewed dataset> --split calibration --criteria "
              + ",".join(CRITERIA) + " --judge-config <judge.json> --baseline-run <id> --baseline-producer <producer.json> "
              "--candidate-run <id> --candidate-producer <producer.json> --acceptance <criteria.json> --out <artifacts>/plan.json",
              "python evaluation/scripts/eval_cli.py init-run --artifacts <artifacts> --dataset <reviewed dataset> --plan <artifacts>/plan.json --arm baseline",
              "python evaluation/scripts/eval_cli.py import-outputs --artifacts <artifacts> --run-id <id> --outputs <outputs.jsonl>",
              "python evaluation/scripts/eval_cli.py assert --artifacts <artifacts> --run-id <id> --dataset <reviewed dataset> --arm baseline --findings-out <artifacts>/baseline-findings.json",
              "python evaluation/scripts/eval_cli.py judge --live --artifacts <artifacts> --run-id <id> --dataset <reviewed dataset> --max-gpu-seconds <cap>   # authorized Modal run only",
              "python evaluation/scripts/eval_cli.py calibrate --artifacts <artifacts> --run-id <id> --labels <human labels.json>",
              "python evaluation/scripts/eval_cli.py compare --artifacts <artifacts> --dataset <reviewed dataset> --baseline <id> --candidate <id> --deterministic <findings.json> --out <dir>",
              "python evaluation/scripts/eval_cli.py export-ax --dataset <reviewed dataset> --split calibration --out <artifacts>/ax_rows.jsonl",
              "python evaluation/scripts/eval_cli.py upload --live ...   # needs a reviewed AX SDK pin and authorization",
              "```", ""]
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args(argv)
    text = render(Path(args.dataset))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
