"""Dataset snapshots/freezing and the append-only artifact store."""

import json
from datetime import datetime, timezone

import pytest

from eval_dataset import (
    FreezeRefusedError,
    HeldoutChangedError,
    LabelStatus,
    ReferenceLabel,
    freeze_heldout,
    load_dataset,
    verify_frozen_heldout,
)
from eval_fixtures import DATASET, FIXTURE_JUDGE, make_run, snapshot
from eval_results import CriterionResult, ErrorCode, Outcome, UnitKey
from eval_store import ArtifactConflictError, RunStore


# --- dataset -------------------------------------------------------------------


def test_the_committed_draft_dataset_loads_and_hashes_deterministically():
    first, second = snapshot(), snapshot()
    assert first.content_hash == second.content_hash
    assert first.snapshot_id.startswith("tutor-reference-v1@")
    assert len(first.cases) == 11
    assert {case.split for case in first.cases} == {"development"}
    assert all(case.permission == "synthetic" for case in first.cases)


def test_no_case_in_the_draft_dataset_claims_a_gold_reference():
    """Author-drafted references are suggestions until humans review them."""

    for case in snapshot().cases:
        assert case.reference is None or case.reference.status is LabelStatus.SUGGESTED


def test_any_content_edit_changes_the_snapshot_hash(tmp_path):
    data = json.loads(DATASET.read_text(encoding="utf-8"))
    data["cases"][0]["instruction"] += " (edited)"
    edited = tmp_path / "edited.json"
    edited.write_text(json.dumps(data), encoding="utf-8")
    assert load_dataset(edited).content_hash != snapshot().content_hash


def test_gold_requires_an_independent_source_checked_reviewer():
    with pytest.raises(ValueError):
        ReferenceLabel(text="x", status=LabelStatus.HUMAN_GOLD, author="a", reviewer="a", source_checked=True)
    with pytest.raises(ValueError):
        ReferenceLabel(text="x", status=LabelStatus.HUMAN_GOLD, author="a", reviewer="b", source_checked=False)
    assert ReferenceLabel(text="x", status=LabelStatus.HUMAN_GOLD, author="a", reviewer="b",
                          source_checked=True).is_gold


def _dataset_with_heldout(tmp_path, status="human_gold"):
    data = json.loads(DATASET.read_text(encoding="utf-8"))
    for case in data["cases"][:3]:
        case["split"] = "heldout"
        case["reference"]["status"] = status
        case["reference"]["reviewer"] = "human-reviewer"
        case["reference"]["source_checked"] = True
    path = tmp_path / "with_heldout.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_freezing_is_refused_without_heldout_cases_or_gold_references(tmp_path):
    with pytest.raises(FreezeRefusedError):
        freeze_heldout(snapshot(), "tester", tmp_path)
    with pytest.raises(FreezeRefusedError):
        freeze_heldout(load_dataset(_dataset_with_heldout(tmp_path, status="suggested")), "tester", tmp_path)


def test_a_frozen_heldout_set_detects_later_edits_and_is_never_overwritten(tmp_path):
    path = _dataset_with_heldout(tmp_path)
    locked = tmp_path / "locked"
    frozen = freeze_heldout(load_dataset(path), "tester", locked)
    assert verify_frozen_heldout(load_dataset(path), locked).heldout_hash == frozen.heldout_hash

    with pytest.raises(FreezeRefusedError):
        freeze_heldout(load_dataset(path), "tester", locked)

    data = json.loads(path.read_text(encoding="utf-8"))
    data["cases"][0]["reference"]["text"] += " tuned"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(HeldoutChangedError):
        verify_frozen_heldout(load_dataset(path), locked)


# --- store ---------------------------------------------------------------------


def _result(store, case_id="ohm-dev-01-graph-sufficient", outcome=Outcome.SCORED, **extra):
    fields = dict(score=4) if outcome is Outcome.SCORED else {}
    fields.update(extra)
    return CriterionResult(
        key=UnitKey(run_id=store.run_id, case_id=case_id, repetition=0, criterion_id="source_support_v1"),
        outcome=outcome, judge_config_id=FIXTURE_JUDGE.judge_config_id, input_hash="h", **fields,
    )


def test_manifest_is_immutable_but_resume_with_the_same_manifest_is_accepted(tmp_path):
    store = make_run(tmp_path, "run-a")
    manifest = store.manifest()
    assert store.create(manifest.model_copy(update={"created_at": datetime.now(timezone.utc)})) == manifest
    with pytest.raises(ArtifactConflictError):
        store.create(manifest.model_copy(update={"repetitions": 2}))


def test_frozen_outputs_cannot_be_replaced(tmp_path):
    store = make_run(tmp_path, "run-a")
    case_id = store.manifest().case_ids[0]
    original = store.outputs()[(case_id, 0)]
    assert store.append_output(case_id, 0, original.response) == original
    with pytest.raises(ArtifactConflictError):
        store.append_output(case_id, 0, "a regenerated, different answer")


def test_terminal_results_are_final_and_failures_can_be_superseded(tmp_path):
    store = make_run(tmp_path, "run-a")
    failed = _result(store, outcome=Outcome.FAILED, error_code=ErrorCode.RATE_LIMITED)
    store.append_result(failed)
    scored = _result(store)
    record = store.append_result(scored)
    assert record.attempt == 2
    assert store.append_result(scored).attempt == 2  # identical re-append is a no-op
    with pytest.raises(ArtifactConflictError):
        store.append_result(_result(store, score=5))
    assert store.effective_results()[scored.key] == scored


def test_results_distinguish_every_non_score_and_reject_contradictions(tmp_path):
    store = make_run(tmp_path, "run-a")
    with pytest.raises(ValueError):
        _result(store, outcome=Outcome.MISSING)  # no reason
    with pytest.raises(ValueError):
        _result(store, outcome=Outcome.INVALID, error_code=ErrorCode.RATE_LIMITED)  # wrong family
    with pytest.raises(ValueError):
        _result(store, outcome=Outcome.FAILED, error_code=ErrorCode.AUTH_FAILED, score=1)


def test_a_torn_final_line_is_ignored_and_redone(tmp_path):
    store = make_run(tmp_path, "run-a")
    store.append_result(_result(store))
    with (store.run_dir / "results.jsonl").open("a", encoding="utf-8") as handle:
        handle.write('{"attempt": 1, "result": {"key"')  # crash mid-append, no newline
    assert len(store.effective_results()) == 1


def test_upload_confirmation_is_final(tmp_path):
    store = make_run(tmp_path, "run-a")
    store.record_upload("dataset:x", "pending")
    store.record_upload("dataset:x", "confirmed", {"dataset_id": "ds-1"})
    assert store.record_upload("dataset:x", "confirmed", {"dataset_id": "ds-1"}).state == "confirmed"
    with pytest.raises(ArtifactConflictError):
        store.record_upload("dataset:x", "pending")


def test_judge_config_identity_changes_with_any_setting():
    assert FIXTURE_JUDGE.judge_config_id != FIXTURE_JUDGE.model_copy(update={"dtype": "float16"}).judge_config_id
    assert FIXTURE_JUDGE.judge_config_id != FIXTURE_JUDGE.model_copy(update={"max_new_tokens": 256}).judge_config_id
    assert RunStore  # imported for the fixtures above
