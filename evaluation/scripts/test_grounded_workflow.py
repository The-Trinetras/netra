"""Offline workflow over netra-grounded-v1: producer boundary, outputs,
assertions, review gates, AX rows, experiment plans and a plumbing run.

Scores and candidate texts here are LABELLED FIXTURES (plumbing_fixtures.py,
eval_fixtures.FakeJudgeTransport); they demonstrate the workflow only.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import eval_cli
import plumbing_fixtures
from ax_upload import dataset_rows
from case_assertions import AssertionOutcome, evaluate_case, evaluate_run, findings, summarize
from comparison import compare_runs
from eval_dataset import DatasetFile, LabelStatus, load_dataset
from eval_fixtures import FIXTURE_JUDGE, FakeJudgeTransport
from eval_results import ErrorCode, Outcome
from eval_store import ArtifactConflictError, ProducerConfig, RunManifest, RunStore
from experiment_plan import ArmPlan, check_run, make_plan, manifest_for
from judge_client import JudgeCallError, RunAllowance
from judge_runner import judge_run, load_rubrics
from producer_io import PRODUCER_FIELDS, ReferenceLeakError, assert_no_leak, import_outputs, producer_inputs
from review_sheet import FIRST_BATCH, MINIMUM_BATCH, ReviewEntry, ReviewFile, ReviewRejectedError, apply_review, render_worksheet, review_template
from grounding import Registry

REPO = Path(__file__).resolve().parents[2]
GROUNDED = REPO / "evaluation" / "datasets" / "netra_grounded_v1.json"
RUBRICS = REPO / "evaluation" / "rubrics"
CRITERIA = ["factual_correctness_v2", "source_support_v2", "citation_correctness_v1", "teaching_usefulness_v2",
            "appropriate_uncertainty_v1"]


@pytest.fixture(scope="module")
def snap():
    return load_dataset(GROUNDED)


def _run(root: Path, run_id: str, snap, split="calibration", source="fixture_replay", label="fixture") -> RunStore:
    rubrics = load_rubrics(RUBRICS, CRITERIA)
    store = RunStore(root, run_id)
    store.create(RunManifest(
        run_id=run_id, dataset_name=snap.dataset_name, dataset_hash=snap.content_hash, split=split,
        case_ids=[case.case_id for case in snap.by_split(split)], repetitions=1, criteria=CRITERIA,
        rubric_hashes={k: v[1] for k, v in rubrics.items()},
        producer=ProducerConfig(label=label, source=source, commit="0" * 40), judge=FIXTURE_JUDGE,
        created_at=datetime.now(timezone.utc)))
    return store


def _write_outputs(path: Path, outputs: dict) -> Path:
    path.write_text("".join(json.dumps({"case_id": case_id, **row}) + "\n" for case_id, row in outputs.items()),
                    encoding="utf-8")
    return path


# --- the producer boundary ----------------------------------------------------


@pytest.mark.parametrize("split", ["development", "calibration", "heldout"])
def test_producer_inputs_carry_only_what_netra_may_see(snap, split):
    registry = Registry.load()
    rows = {row["case_id"]: row for row in producer_inputs(snap, split)}
    assert len(rows) == len(snap.by_split(split))
    for case in snap.by_split(split):
        row = rows[case.case_id]
        assert set(row) == set(PRODUCER_FIELDS)
        blob = json.dumps(row, ensure_ascii=False)
        if case.reference is not None:
            assert case.reference.text not in blob
        for held in case.withheld_evidence:
            assert held.evidence_id not in {item["evidence_id"] for item in row["evidence"]}
            assert registry.evidence(held.evidence_id, held.source_version_id)[1]["text"] not in blob
    everything = json.dumps(list(rows.values()), ensure_ascii=False)
    for forbidden in ("rationale", "acceptable_alternatives", "assertions", "expected_behavior", "calculations",
                      "withheld_evidence", "candidate_fixture", "failure_modes", "category"):
        assert f'"{forbidden}"' not in everything


def test_a_reference_or_withheld_leak_into_producer_inputs_is_refused(snap):
    registry = Registry.load()
    case = snap.case("pack-11-assisted-correct-answer")
    rows = [{"case_id": case.case_id, "student_input": case.reference.text, "conversation": [],
             "session_context": None, "evidence": []}]
    with pytest.raises(ReferenceLeakError):
        assert_no_leak([case], rows, registry)

    denied = snap.case("ch4-20-other-students-note")
    held = denied.withheld_evidence[0]
    text = registry.evidence(held.evidence_id, held.source_version_id)[1]["text"]
    leaked = [{"case_id": denied.case_id, "student_input": denied.student_input, "conversation": [],
               "session_context": None, "evidence": [{"evidence_id": held.evidence_id, "text": text}]}]
    with pytest.raises(ReferenceLeakError):
        assert_no_leak([denied], leaked, registry)


# --- outputs --------------------------------------------------------------------


def test_imported_outputs_are_labelled_by_the_run_not_by_the_caller(tmp_path, snap):
    netra_run = _run(tmp_path, "netra-run", snap, source="netra", label="netra-baseline")
    fixture_run = _run(tmp_path, "fixture-run", snap)
    outputs = _write_outputs(tmp_path / "out.jsonl", {"pack-07-which-object-gives-2-ohms": plumbing_fixtures.BASELINE["pack-07-which-object-gives-2-ohms"]})
    import_outputs(netra_run, outputs)
    import_outputs(fixture_run, outputs)
    assert netra_run.outputs()[("pack-07-which-object-gives-2-ohms", 0)].origin == "netra"
    assert fixture_run.outputs()[("pack-07-which-object-gives-2-ohms", 0)].origin == "fixture"
    assert import_outputs(fixture_run, outputs)["already_frozen"] == 1  # idempotent


def test_outputs_for_units_outside_the_run_or_with_unknown_fields_are_refused(tmp_path, snap):
    store = _run(tmp_path, "r", snap)
    with pytest.raises(ArtifactConflictError):
        import_outputs(store, _write_outputs(tmp_path / "a.jsonl", {"ch4-13-resistance-from-table": {"response": "x"}}))
    (tmp_path / "b.jsonl").write_text(json.dumps({"case_id": "lamp-01-is-it-ohmic", "response": "x",
                                                  "reference": "leak"}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        import_outputs(store, tmp_path / "b.jsonl")


# --- deterministic assertions ---------------------------------------------------------


def test_assertions_distinguish_passed_failed_not_evaluable_and_missing(tmp_path, snap):
    store = _run(tmp_path, "cand", snap)
    import_outputs(store, _write_outputs(tmp_path / "c.jsonl", plumbing_fixtures.CANDIDATE))
    results = evaluate_run(store, snap)
    summary = summarize(results)
    assert set(summary["by_outcome"]) == {"passed", "failed", "not_evaluable", "missing_output"}
    critical = "\n".join(summary["critical_failures"])
    assert "pack-10-hint-after-reasoning" in critical and "8 V" in critical        # leaked pending answer
    assert "m1-01-injection-in-retrieved-page" in critical and "ev-other-student" in critical
    assert any("pack-12-declined-check" in f and "1 proposed learning event" in f for f in summary["failures"])
    pack07 = [r for r in results if r.case_id == "pack-07-which-object-gives-2-ohms"]
    assert {r.outcome for r in pack07 if r.assertion_type in ("must_cite_any", "cites_only_supplied")} == {AssertionOutcome.NOT_EVALUABLE}
    assert {r.outcome for r in results if r.case_id == "lamp-02-always-2-ohms"} == {AssertionOutcome.MISSING_OUTPUT}
    decided = findings(results, "candidate")
    assert all(r.outcome in (AssertionOutcome.PASSED, AssertionOutcome.FAILED) for r in results
               if any(f.case_id == r.case_id and f.check_id == r.check_id for f in decided))


def test_every_draft_reference_passes_its_own_text_assertions(snap):
    from eval_store import FrozenOutput

    for case in snap.cases:
        if case.reference is None:
            continue
        probe = FrozenOutput(run_id="probe", case_id=case.case_id, repetition=0, response=case.reference.text,
                             response_hash="n/a", generated_at=datetime.now(timezone.utc))
        failed = [r for r in evaluate_case(case, probe) if r.outcome is AssertionOutcome.FAILED]
        assert failed == [], case.case_id


# --- human review gates -------------------------------------------------------------


def test_the_worksheet_and_template_never_prefill_a_reviewer(snap):
    sheet = render_worksheet(snap, Registry.load(), FIRST_BATCH)
    template = review_template(snap, FIRST_BATCH)
    assert all(entry.reviewer is None and entry.decision is None and not entry.source_checked for entry in template.entries)
    assert [entry.case_id for entry in template.entries[:len(FIRST_BATCH)]] == FIRST_BATCH
    assert len(template.entries) == len(snap.cases)
    assert sheet.count("Reviewer name: ______") == len(snap.cases)
    assert all(snap.case(case_id).split == "calibration" for case_id in MINIMUM_BATCH) and len(MINIMUM_BATCH) == 10


def _dataset():
    return DatasetFile.model_validate(json.loads(GROUNDED.read_text(encoding="utf-8")))


def test_approval_needs_a_distinct_named_reviewer_and_a_source_check(snap):
    def review(**entry):
        return ReviewFile(dataset=snap.snapshot_id, instructions="t",
                          entries=[ReviewEntry(case_id="lamp-01-is-it-ohmic", reviewed_on="2026-09-20", **entry)])

    with pytest.raises(ReviewRejectedError):
        apply_review(_dataset(), snap, review(decision="approve", reviewer="R. Reviewer", source_checked=False), "x")
    with pytest.raises(ValueError):  # the author approving their own draft cannot become gold
        apply_review(_dataset(), snap, review(decision="approve", reviewer=snap.case("lamp-01-is-it-ohmic").reference.author,
                                              source_checked=True), "x")
    reviewed = apply_review(_dataset(), snap, review(decision="approve", reviewer="R. Reviewer", source_checked=True),
                            "netra-grounded-v1r1")
    case = next(c for c in reviewed.cases if c.case_id == "lamp-01-is-it-ohmic")
    assert case.reference.status is LabelStatus.HUMAN_GOLD and case.review_status == "approved"
    assert reviewed.dataset_name == "netra-grounded-v1r1"


def test_a_revision_stays_suggested_until_a_second_reviewer_and_reject_is_kept(snap):
    review = ReviewFile(dataset=snap.snapshot_id, instructions="t", entries=[
        ReviewEntry(case_id="lamp-02-always-2-ohms", decision="revise", corrected_reference="Better text.",
                    reviewer="R. Reviewer", reviewed_on="2026-09-20", source_checked=True),
        ReviewEntry(case_id="pack-06-equation-failed-check", decision="reject", reviewer="R. Reviewer",
                    reviewed_on="2026-09-20")])
    reviewed = {c.case_id: c for c in apply_review(_dataset(), snap, review, "netra-grounded-v1r1").cases}
    revised = reviewed["lamp-02-always-2-ohms"].reference
    assert revised.status is LabelStatus.SUGGESTED and revised.author == "R. Reviewer" and revised.reviewer is None
    assert reviewed["pack-06-equation-failed-check"].review_status == "rejected"
    with pytest.raises(ReviewRejectedError):
        apply_review(_dataset(), snap, review, "netra-grounded-v1")  # same name: not a new version


# --- AX rows ----------------------------------------------------------------------------


def test_ax_rows_carry_grounding_but_never_withheld_text(snap):
    rows = dataset_rows(snap, [case.case_id for case in snap.cases])
    row = next(r for r in rows if r["case_id"] == "ch4-20-other-students-note")
    assert row["problem_family"] == "intro-circuits-ch4" and row["prior_exposure"] is True
    assert json.loads(row["withheld_evidence"])[0]["reason"] == "denied_other_account"
    assert "Another student's private note about" not in json.dumps(rows, ensure_ascii=False)
    rejected = snap.case("lamp-01-is-it-ohmic").model_copy(update={"review_status": "rejected"})
    from eval_dataset import DatasetSnapshot

    with pytest.raises(ValueError):
        dataset_rows(DatasetSnapshot(dataset_name="t", content_hash="h", cases=(rejected,)), ["lamp-01-is-it-ohmic"])


# --- experiment plans and the held-out guard --------------------------------------------


def test_a_plan_pins_both_arms_and_detects_a_mismatched_run(tmp_path, snap):
    rubrics = {k: v[1] for k, v in load_rubrics(RUBRICS, CRITERIA).items()}
    plan = make_plan(snap, "calibration", CRITERIA, rubrics, FIXTURE_JUDGE,
                     ArmPlan(run_id="base", producer=ProducerConfig(label="b", source="netra", commit="a" * 40)),
                     ArmPlan(run_id="cand", producer=ProducerConfig(label="c", source="netra", commit="b" * 40)),
                     acceptance_criteria=["No new critical deterministic failure."])
    assert plan.plan_id == plan.model_copy(update={"created_at": datetime.now(timezone.utc)}).plan_id
    assert check_run(plan, "baseline", manifest_for(plan, "baseline")) == []
    other = manifest_for(plan, "candidate").model_copy(update={"criteria": CRITERIA[:2]})
    assert "criteria differs from the plan" in check_run(plan, "candidate", other)


def test_init_run_refuses_unfrozen_heldout_cases(tmp_path, capsys):
    producer = tmp_path / "producer.json"
    producer.write_text(ProducerConfig(label="p", source="netra", commit="a" * 40).model_dump_json(), encoding="utf-8")
    judge = tmp_path / "judge.json"
    judge.write_text(FIXTURE_JUDGE.model_dump_json(), encoding="utf-8")
    code = eval_cli.main(["init-run", "--artifacts", str(tmp_path), "--dataset", str(GROUNDED), "--run-id", "h",
                          "--split", "heldout", "--criteria", ",".join(CRITERIA), "--producer", str(producer),
                          "--judge-config", str(judge)])
    assert code == 2 and "refused" in capsys.readouterr().err
    assert not (tmp_path / "h").exists()


# --- plumbing: assertions + scripted judge + resume + comparison ---------------------------


async def test_the_workflow_runs_end_to_end_on_labelled_fixtures(tmp_path, snap):
    rubrics = load_rubrics(RUBRICS, CRITERIA)
    base, cand = _run(tmp_path, "base", snap), _run(tmp_path, "cand", snap)
    import_outputs(base, _write_outputs(tmp_path / "b.jsonl", plumbing_fixtures.BASELINE))
    import_outputs(cand, _write_outputs(tmp_path / "c.jsonl", plumbing_fixtures.CANDIDATE))

    def script(request):
        if "lamp-01" in request.request_id and "citation" in request.request_id:
            return "Feedback: no marker here"                                   # invalid judge output
        if "pack-03" in request.request_id and "teaching" in request.request_id:
            return JudgeCallError(ErrorCode.SERVER_ERROR)                        # failed call
        return None                                                              # scripted "[RESULT] 3"

    allowance = RunAllowance(max_gpu_seconds=10_000, margin_seconds=0, per_call_estimate_seconds=1)
    first = await judge_run(base, snap, rubrics, FakeJudgeTransport(script), allowance)
    assert first.invalid == 1 and first.failed == 1
    # Resume: only the failed unit is retried; the invalid one is terminal and
    # every finished unit is skipped without a judge call.
    resumed = FakeJudgeTransport()
    again = await judge_run(base, snap, rubrics, resumed, allowance)
    units = len(snap.by_split("calibration")) * len(CRITERIA)
    assert first.judged == units - 1  # every dispatched unit except the failed call
    assert again.judged == 1 and len(resumed.score_calls) == 1 and again.skipped_terminal == first.judged
    await judge_run(cand, snap, rubrics, FakeJudgeTransport(), allowance)

    outcomes = {(k.case_id, k.criterion_id): r.outcome for k, r in base.effective_results().items()}
    assert outcomes[("lamp-01-is-it-ohmic", "citation_correctness_v1")] is Outcome.INVALID

    deterministic = findings(evaluate_run(base, snap), "baseline") + findings(evaluate_run(cand, snap), "candidate")
    report = compare_runs(base, cand, snap, REPO / "evaluation" / "locked", deterministic)
    assert report.verdict == "blocked_by_critical_deterministic_failures"
    assert {f.case_id for f in report.critical_failures} >= {"pack-10-hint-after-reasoning", "m1-01-injection-in-retrieved-page"}
    per = report.per_criterion()
    assert all(entry["missing_candidate"] >= 1 for entry in per.values())  # lamp-02 has no candidate output
