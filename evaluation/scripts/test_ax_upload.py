"""AX upload recovery with a labelled in-memory AX double (no SDK, no network)."""

import pytest

from ax_upload import (
    AmbiguousUploadError,
    CreatedObject,
    LookupNotSupportedError,
    PendingAxSdkClient,
    UploadRejectedError,
    dataset_name,
    experiment_rows,
    upload_run,
)
from eval_fixtures import FakeJudgeTransport, make_run, rubrics, snapshot
from judge_client import RunAllowance
from judge_runner import judge_run


class FakeAx:
    """Stand-in for an AX client. ``fail`` scripts failures per call name."""

    def __init__(self, fail=None, lookup=True):
        self.fail = dict(fail or {})
        self.lookup = lookup
        self.datasets: dict[str, CreatedObject] = {}
        self.experiments: dict[tuple[str, str], CreatedObject] = {}
        self.calls: list[str] = []

    def _maybe_fail(self, name, apply):
        self.calls.append(name)
        failure = self.fail.pop(name, None)
        if failure == "ambiguous_after_apply":
            apply()
            raise AmbiguousUploadError(name)
        if failure == "ambiguous_before_apply":
            raise AmbiguousUploadError(name)
        if failure == "rejected":
            raise UploadRejectedError("validation_failed")
        return apply()

    def find_dataset(self, name):
        self.calls.append("find_dataset")
        if not self.lookup:
            raise LookupNotSupportedError()
        return self.datasets.get(name)

    def create_dataset(self, name, rows):
        def apply():
            created = CreatedObject(f"ds-{len(self.datasets) + 1}", {r["row_key"]: f"ex-{r['row_key']}" for r in rows})
            self.datasets[name] = created
            return created
        return self._maybe_fail("create_dataset", apply)

    def find_experiment(self, dataset_id, name):
        self.calls.append("find_experiment")
        if not self.lookup:
            raise LookupNotSupportedError()
        return self.experiments.get((dataset_id, name))

    def create_experiment(self, dataset_id, name, rows):
        def apply():
            created = CreatedObject(f"exp-{len(self.experiments) + 1}")
            self.experiments[(dataset_id, name)] = created
            self.last_rows = rows
            return created
        return self._maybe_fail("create_experiment", apply)


async def _judged_run(tmp_path):
    store = make_run(tmp_path, "run-a", criteria=["source_support_v1"],
                     case_ids=["ohm-dev-01-graph-sufficient", "ohm-dev-11-reference-pending"])
    await judge_run(store, snapshot(), rubrics(["source_support_v1"]), FakeJudgeTransport(),
                    RunAllowance(max_gpu_seconds=100, margin_seconds=0, per_call_estimate_seconds=1))
    return store


async def test_upload_confirms_and_records_every_external_id(tmp_path):
    store = await _judged_run(tmp_path)
    ax = FakeAx()
    summary = upload_run(store, snapshot(), ax)

    assert summary.confirmed == [f"dataset:{snapshot().snapshot_id}", "experiment:run-a"]
    states = store.upload_states()
    dataset_ids = states[f"dataset:{snapshot().snapshot_id}"].external_ids
    assert dataset_ids["dataset_id"] == "ds-1"
    assert dataset_ids["example:ohm-dev-01-graph-sufficient"] == "ex-ohm-dev-01-graph-sufficient"
    assert states["experiment:run-a"].external_ids == {"experiment_id": "exp-1"}


async def test_repeating_a_confirmed_upload_sends_nothing(tmp_path):
    store = await _judged_run(tmp_path)
    upload_run(store, snapshot(), FakeAx())
    again = FakeAx()
    upload_run(store, snapshot(), again)
    assert again.calls == []


async def test_an_ambiguous_create_that_actually_landed_is_reconciled_not_duplicated(tmp_path):
    store = await _judged_run(tmp_path)
    ax = FakeAx(fail={"create_dataset": "ambiguous_after_apply"})
    first = upload_run(store, snapshot(), ax)
    assert first.stopped and first.ambiguous == [f"dataset:{snapshot().snapshot_id}"]

    second = upload_run(store, snapshot(), ax)
    assert second.reconciled == [f"dataset:{snapshot().snapshot_id}"]
    assert len(ax.datasets) == 1
    assert ax.calls.count("create_dataset") == 1


async def test_an_ambiguous_create_that_never_landed_is_retried_after_lookup(tmp_path):
    store = await _judged_run(tmp_path)
    ax = FakeAx(fail={"create_experiment": "ambiguous_before_apply"})
    upload_run(store, snapshot(), ax)
    summary = upload_run(store, snapshot(), ax)

    assert "experiment:run-a" in summary.confirmed
    assert ax.calls.count("create_experiment") == 2 and len(ax.experiments) == 1


async def test_without_lookup_an_ambiguous_upload_stops_for_an_operator(tmp_path):
    store = await _judged_run(tmp_path)
    ax = FakeAx(fail={"create_dataset": "ambiguous_after_apply"}, lookup=False)
    upload_run(store, snapshot(), ax)
    summary = upload_run(store, snapshot(), ax)

    assert summary.stopped and summary.ambiguous
    assert ax.calls.count("create_dataset") == 1  # never blindly re-created


async def test_a_rejected_upload_is_recorded_as_an_error(tmp_path):
    store = await _judged_run(tmp_path)
    summary = upload_run(store, snapshot(), FakeAx(fail={"create_dataset": "rejected"}))
    assert summary.errors == {f"dataset:{snapshot().snapshot_id}": "validation_failed"}


async def test_experiment_rows_keep_non_scores_explicit_and_carry_ids(tmp_path):
    store = await _judged_run(tmp_path)
    rows = {row["case_id"]: row for row in experiment_rows(store)}

    assert rows["ohm-dev-01-graph-sufficient"]["source_support_v1.outcome"] == "scored"
    pending = rows["ohm-dev-11-reference-pending"]
    assert pending["source_support_v1.outcome"] == "missing"
    assert pending["source_support_v1.score"] is None
    assert pending["source_support_v1.error"] == "reference_pending"
    assert rows["ohm-dev-01-graph-sufficient"]["judge_config_id"].startswith("judge-")


def test_names_are_deterministic_from_the_snapshot_hash():
    assert dataset_name(snapshot()) == dataset_name(snapshot())
    assert snapshot().content_hash[:16] in dataset_name(snapshot())


async def test_the_real_sdk_client_fails_closed_until_a_reviewed_pin_exists(tmp_path):
    store = await _judged_run(tmp_path)
    with pytest.raises(NotImplementedError):
        upload_run(store, snapshot(), PendingAxSdkClient())
