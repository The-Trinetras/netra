"""Calibration, paired comparison and Ragas-style metrics (fixture data only).

All judge scores here are scripted by FakeJudgeTransport. These tests prove
the statistics and refusal rules, not the judge's reliability.
"""

import json

import pytest

from calibration import CALIBRATION_FLOOR, CalibrationRefusedError, HumanLabel, calibrate
from comparison import (
    ComparisonIdentityError,
    DeterministicFinding,
    PairwiseJudgement,
    compare_runs,
    pairwise_consistency,
    render_markdown,
    rescore_manifest,
)
from eval_dataset import HeldoutChangedError, load_dataset
from eval_fixtures import DATASET, FIXTURE_JUDGE, FakeJudgeTransport, make_run, rubrics, snapshot
from judge_client import RunAllowance
from judge_runner import judge_run
from ragas_style import cited_evidence_support, context_precision, context_recall

CASES = ["ohm-dev-01-graph-sufficient", "ohm-dev-02-axes-swapped", "ohm-dev-03-invented-point"]


def _allowance():
    return RunAllowance(max_gpu_seconds=1000, margin_seconds=0, per_call_estimate_seconds=1)


def _scripted(scores):
    """scores: case_id -> score text for source_support_v1."""

    def script(request):
        for case_id, score in scores.items():
            if f"/{case_id}/" in request.request_id:
                return score if not score.isdigit() else f"Feedback: scripted. [RESULT] {score}"
        return None

    return FakeJudgeTransport(script)


async def _run(tmp_path, run_id, scores, criteria=("source_support_v1",), judge=FIXTURE_JUDGE, cases=CASES):
    store = make_run(tmp_path, run_id, criteria=list(criteria), case_ids=list(cases), judge=judge)
    await judge_run(store, snapshot(), rubrics(list(criteria)), _scripted(scores), _allowance())
    return store


# --- calibration -----------------------------------------------------------------


async def test_calibration_reports_agreement_error_and_false_highs(tmp_path):
    store = await _run(tmp_path, "cal", {CASES[0]: "5", CASES[1]: "4", CASES[2]: "2"})
    labels = [
        HumanLabel(case_id=CASES[0], criterion_id="source_support_v1", score=5, labeller="h1"),
        HumanLabel(case_id=CASES[1], criterion_id="source_support_v1", score=1, labeller="h1"),  # swapped axes
        HumanLabel(case_id=CASES[2], criterion_id="source_support_v1", score=3, labeller="h1",
                   adjudicated_score=1, adjudicator="h2"),
    ]
    entry = calibrate(store, labels)["source_support_v1"]

    assert (entry.compared, entry.exact, entry.within_one) == (3, 1, 2)
    assert entry.mean_abs_error == pytest.approx((0 + 3 + 1) / 3)
    assert entry.false_high == [f"{CASES[1]}/r0"]  # judge 4 on an unsupported answer
    assert entry.confusion["1->4"] == 1 and entry.confusion["1->2"] == 1  # adjudicated score used
    assert entry.below_floor and CALIBRATION_FLOOR == 10


async def test_unscored_judge_outcomes_are_counted_not_imputed(tmp_path):
    store = await _run(tmp_path, "cal", {CASES[0]: "Feedback: no marker"})
    labels = [HumanLabel(case_id=CASES[0], criterion_id="source_support_v1", score=4, labeller="h1")]
    entry = calibrate(store, labels)["source_support_v1"]
    assert entry.compared == 0
    assert entry.judge_not_scored == {"invalid:no_result_marker": 1}
    assert entry.exact_rate is None


async def test_calibration_on_heldout_runs_is_refused(tmp_path):
    data = json.loads(DATASET.read_text(encoding="utf-8"))
    data["cases"][0]["split"] = "heldout"
    path = tmp_path / "d.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    snap = load_dataset(path)
    store = make_run(tmp_path, "held", snap=snap, criteria=["source_support_v1"], split="heldout")
    with pytest.raises(CalibrationRefusedError):
        calibrate(store, [])


# --- comparison ------------------------------------------------------------------


async def test_paired_comparison_reports_deltas_regressions_and_denominators(tmp_path):
    base = await _run(tmp_path, "base", {CASES[0]: "3", CASES[1]: "4", CASES[2]: "2"})
    cand = await _run(tmp_path, "cand", {CASES[0]: "4", CASES[1]: "2", CASES[2]: "Feedback: truncated"})
    report = compare_runs(base, cand, snapshot(), tmp_path / "locked")

    per = report.per_criterion()["source_support_v1"]
    assert per["expected_pairs"] == 3 and per["complete_pairs"] == 2
    assert per["missing_candidate"] == 1 and per["improved"] == 1 and per["regressed"] == 1
    assert [r.case_id for r in report.regressions] == [CASES[1]]
    assert report.verdict == "partial_comparison_incomplete_pairs"
    markdown = render_markdown(report)
    assert "Incomplete pairs" in markdown and "invalid:no_result_marker" in markdown


async def test_a_critical_deterministic_failure_blocks_any_improved_reading(tmp_path):
    base = await _run(tmp_path, "base", {c: "2" for c in CASES})
    cand = await _run(tmp_path, "cand", {c: "5" for c in CASES})
    failure = DeterministicFinding(arm="candidate", case_id=CASES[0], check_id="answer_key_isolation",
                                   passed=False, critical=True, detail="rubric text reached a public segment")
    report = compare_runs(base, cand, snapshot(), tmp_path / "locked", [failure],
                          {c: {"baseline": "complete", "candidate": "complete"} for c in CASES})
    assert report.verdict == "blocked_by_critical_deterministic_failures"
    assert report.per_criterion()["source_support_v1"]["improved"] == 3  # scores alone would look better


async def test_trace_completeness_is_part_of_the_reading(tmp_path):
    base = await _run(tmp_path, "base", {c: "3" for c in CASES})
    cand = await _run(tmp_path, "cand", {c: "3" for c in CASES})
    unknown = compare_runs(base, cand, snapshot(), tmp_path / "locked")
    assert unknown.verdict == "scores_complete_trace_status_unknown"

    partial = {c: {"baseline": "complete", "candidate": "complete"} for c in CASES}
    partial[CASES[1]] = {"baseline": "complete", "candidate": "incomplete"}
    report = compare_runs(base, cand, snapshot(), tmp_path / "locked", [], partial)
    assert report.verdict == "scores_complete_traces_incomplete"


async def test_runs_under_different_judge_settings_are_not_comparable(tmp_path):
    base = await _run(tmp_path, "base", {})
    cand = await _run(tmp_path, "cand", {}, judge=FIXTURE_JUDGE.model_copy(update={"max_new_tokens": 256}))
    with pytest.raises(ComparisonIdentityError):
        compare_runs(base, cand, snapshot(), tmp_path / "locked")


async def test_rescoring_keeps_frozen_outputs_under_a_new_identity(tmp_path):
    base = await _run(tmp_path, "base", {})
    new_judge = FIXTURE_JUDGE.model_copy(update={"dtype": "float16"})
    manifest = rescore_manifest(base.manifest(), "base-rescore", new_judge, base.manifest().rubric_hashes)
    assert manifest.judge.judge_config_id != base.manifest().judge.judge_config_id
    assert manifest.case_ids == base.manifest().case_ids and manifest.producer == base.manifest().producer


async def test_heldout_comparisons_require_a_matching_freeze(tmp_path):
    data = json.loads(DATASET.read_text(encoding="utf-8"))
    for case in data["cases"][:3]:
        case["split"] = "heldout"
    path = tmp_path / "d.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    snap = load_dataset(path)
    base = make_run(tmp_path, "b", snap=snap, criteria=["source_support_v1"], split="heldout")
    cand = make_run(tmp_path, "c", snap=snap, criteria=["source_support_v1"], split="heldout")
    with pytest.raises(HeldoutChangedError):
        compare_runs(base, cand, snap, tmp_path / "locked")


def test_pairwise_preferences_must_agree_in_both_orders():
    judgements = [
        PairwiseJudgement("c1", "k", "baseline_first", "B"), PairwiseJudgement("c1", "k", "candidate_first", "A"),
        PairwiseJudgement("c2", "k", "baseline_first", "A"), PairwiseJudgement("c2", "k", "candidate_first", "A"),
        PairwiseJudgement("c3", "k", "baseline_first", None), PairwiseJudgement("c3", "k", "candidate_first", "B"),
    ]
    result = pairwise_consistency(judgements)
    assert result["prefers_candidate"] == ["c1/k"]
    assert sorted(result["needs_human_review"]) == ["c2/k", "c3/k"]  # position bias; invalid output


# --- Ragas-style metrics ------------------------------------------------------------


def test_ragas_style_metrics_need_labels_and_never_default_to_zero():
    assert not context_precision(["a"], None).evaluated
    assert not context_recall(["a"], None).evaluated
    assert not cited_evidence_support([], {"a"}).evaluated
    assert context_precision(["a", "x", "b"], {"a", "b"}).value == pytest.approx((1 / 1 + 2 / 3) / 2)
    assert context_recall(["a", "x"], {"a", "b"}).value == 0.5
    assert cited_evidence_support(["a", "x"], {"a"}).value == 0.5
