"""Upload persisted datasets/results to Arize AX, with ambiguous-upload recovery.

AX plan, "Reproducible datasets, experiments and recovery":

- Uploads read ONLY persisted artifacts (dataset snapshot, frozen outputs,
  stored results). This module has no access to a producer or judge, so an
  upload retry can never re-run Netra or spend another judge call.
- Every AX object is created under a deterministic name derived from the
  snapshot hash or run id, and every attempt is recorded in uploads.jsonl
  before and after the call.
- A timeout/transport failure after sending is AMBIGUOUS: the object may
  exist. The next upload first looks it up by its deterministic name; if
  found, it is confirmed with the returned ids; if not, creation is
  retried. If the client cannot look objects up, the upload stops with the
  object left ambiguous for an operator — it is never blindly duplicated.
- External ids (dataset, experiment, per-example) are recorded so case,
  run, experiment and trace ids can be mapped. Names alone are not enough.

The AX SDK is not pinned or installed (docs/team/dependency-review.md):
the concrete client must be written against a reviewed SDK version in the
isolated evaluation environment and keep SDK objects behind AxClient.
PendingAxSdkClient fails closed until then.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

from eval_dataset import DatasetSnapshot
from eval_results import Outcome
from eval_store import RunStore


class AmbiguousUploadError(Exception):
    """The request may have been applied (timeout or connection loss after send)."""


class UploadRejectedError(Exception):
    """AX definitively refused the request (validation, auth, quota)."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class LookupNotSupportedError(Exception):
    pass


@dataclass(frozen=True)
class CreatedObject:
    object_id: str
    example_ids: dict[str, str] = field(default_factory=dict)
    """Our stable row key -> AX example id, when the API returns them."""


class AxClient(Protocol):
    def find_dataset(self, name: str) -> Optional[CreatedObject]:
        ...

    def create_dataset(self, name: str, rows: list[dict[str, Any]]) -> CreatedObject:
        ...

    def find_experiment(self, dataset_id: str, name: str) -> Optional[CreatedObject]:
        ...

    def create_experiment(self, dataset_id: str, name: str, rows: list[dict[str, Any]]) -> CreatedObject:
        ...


class PendingAxSdkClient:
    """Fails closed: no reviewed AX SDK pin exists yet."""

    def _refuse(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError(
            "AX SDK client pending: pin a reviewed arize SDK version in the isolated "
            "evaluation environment (M2 review) and implement AxClient against it"
        )

    find_dataset = create_dataset = find_experiment = create_experiment = _refuse


def dataset_name(snapshot: DatasetSnapshot) -> str:
    return f"netra-{snapshot.dataset_name}-{snapshot.content_hash[:16]}"


def experiment_name(run_id: str) -> str:
    return f"netra-run-{run_id}"


def dataset_rows(snapshot: DatasetSnapshot, case_ids: list[str]) -> list[dict[str, Any]]:
    """One flat row per case. Nested values are JSON strings so the row maps
    onto a plain AX dataset table; stable keys come from the case id."""

    rows = []
    for case_id in case_ids:
        case = snapshot.case(case_id)
        if case.review_status == "rejected":
            raise ValueError(f"{case_id} was rejected in review and must not be uploaded")
        row: dict[str, Any] = {
            "row_key": case.case_id,
            "case_id": case.case_id,
            "split": case.split,
            "kind": case.kind,
            "failure_modes": ",".join(case.failure_modes),
            "instruction": case.instruction,
            "reference": case.reference.text if case.reference else None,
            "reference_status": case.reference.status.value if case.reference else "missing",
            "dataset_hash": snapshot.content_hash,
            "permission": case.permission,
            "criteria": ",".join(case.criteria),
            "evidence_ids": ",".join(item.evidence_id for item in case.source_excerpts),
            "source_version_ids": ",".join(sorted({item.source_version_id for item in case.source_excerpts})),
            "evidence": _json([{"evidence_id": item.evidence_id, "source_version_id": item.source_version_id,
                                "locator": item.locator, "trust": item.trust, "text": item.text}
                               for item in case.source_excerpts]),
        }
        if case.problem_family is not None:
            row.update({
                "category": case.category,
                "problem_family": case.problem_family,
                "expected_behavior": case.expected_behavior,
                "student_input": case.student_input,
                "conversation": _json([turn.model_dump(mode="json") for turn in case.conversation]),
                # Ids and reasons only: withheld text never leaves the repository.
                "withheld_evidence": _json([{"evidence_id": h.evidence_id, "reason": h.reason}
                                            for h in case.withheld_evidence]),
                "reference_author": case.reference.author if case.reference else None,
                "reference_reviewer": case.reference.reviewer if case.reference else None,
                "reference_rationale": case.reference.rationale if case.reference else None,
                "acceptable_alternatives": _json(case.reference.acceptable_alternatives if case.reference else []),
                "assertions": _json([a.model_dump(mode="json") for a in case.assertions]),
                "provenance_origin": case.provenance.origin if case.provenance else None,
                "prior_exposure": bool(case.prior_exposure),
                "review_status": case.review_status,
            })
        rows.append(row)
    return rows


def _json(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def export_dataset_rows(snapshot: DatasetSnapshot, split: str, path) -> dict[str, Any]:
    """Write exactly the rows an upload would send, locally, without any network call."""

    import json
    from pathlib import Path

    case_ids = [case.case_id for case in snapshot.by_split(split)]  # type: ignore[arg-type]
    rows = dataset_rows(snapshot, case_ids)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
                      encoding="utf-8")
    return {"ax_dataset_name": dataset_name(snapshot), "split": split, "rows": len(rows), "path": str(target),
            "columns": sorted({key for row in rows for key in row})}


def experiment_rows(store: RunStore) -> list[dict[str, Any]]:
    """One row per (case, repetition): the frozen output plus every criterion
    outcome, with non-scores kept explicit rather than dropped or zeroed."""

    manifest = store.manifest()
    outputs = store.outputs()
    results = store.effective_results()
    rows = []
    for case_id in manifest.case_ids:
        for repetition in range(manifest.repetitions):
            output = outputs.get((case_id, repetition))
            row: dict[str, Any] = {
                "row_key": f"{case_id}/r{repetition}",
                "case_id": case_id,
                "repetition": repetition,
                "run_id": manifest.run_id,
                "judge_config_id": manifest.judge.judge_config_id,
                "producer": manifest.producer.label,
                "output": output.response if output else None,
                "trace_id": output.trace_id if output else None,
            }
            for criterion_id in manifest.criteria:
                result = next(
                    (r for k, r in results.items()
                     if k.case_id == case_id and k.repetition == repetition and k.criterion_id == criterion_id),
                    None,
                )
                row[f"{criterion_id}.outcome"] = result.outcome.value if result else "pending"
                row[f"{criterion_id}.score"] = result.score if result and result.outcome is Outcome.SCORED else None
                row[f"{criterion_id}.error"] = result.error_code.value if result and result.error_code else None
                row[f"{criterion_id}.feedback"] = result.feedback if result else None
            rows.append(row)
    return rows


@dataclass
class UploadSummary:
    confirmed: list[str] = field(default_factory=list)
    reconciled: list[str] = field(default_factory=list)
    ambiguous: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    stopped: bool = False


def _ids(created: CreatedObject, id_name: str) -> dict[str, str]:
    ids = {id_name: created.object_id}
    ids.update({f"example:{key}": value for key, value in created.example_ids.items()})
    return ids


def _upload_one(store, summary, object_key, id_name, find, create) -> Optional[str]:
    state = store.upload_states().get(object_key)
    if state is not None and state.state == "confirmed":
        summary.confirmed.append(object_key)
        return state.external_ids[id_name]

    if state is not None and state.state in ("pending", "ambiguous"):
        try:
            found = find()
        except LookupNotSupportedError:
            store.record_upload(object_key, "ambiguous", error_code="lookup_unsupported")
            summary.ambiguous.append(object_key)
            summary.stopped = True
            return None
        if found is not None:
            store.record_upload(object_key, "confirmed", _ids(found, id_name))
            summary.reconciled.append(object_key)
            return found.object_id

    store.record_upload(object_key, "pending")
    try:
        created = create()
    except AmbiguousUploadError:
        store.record_upload(object_key, "ambiguous", error_code="ambiguous_timeout")
        summary.ambiguous.append(object_key)
        summary.stopped = True
        return None
    except UploadRejectedError as rejected:
        store.record_upload(object_key, "error", error_code=rejected.code)
        summary.errors[object_key] = rejected.code
        summary.stopped = True
        return None
    store.record_upload(object_key, "confirmed", _ids(created, id_name))
    summary.confirmed.append(object_key)
    return created.object_id


def upload_run(store: RunStore, snapshot: DatasetSnapshot, client: AxClient) -> UploadSummary:
    """Upload the run's dataset and experiment from persisted artifacts only.

    Safe to repeat: confirmed objects are not re-sent; ambiguous ones are
    reconciled by lookup first.
    """

    manifest = store.manifest()
    if manifest.dataset_hash != snapshot.content_hash:
        raise ValueError("the snapshot does not match the run's dataset")
    summary = UploadSummary()

    ds_name = dataset_name(snapshot)
    dataset_id = _upload_one(
        store, summary, f"dataset:{snapshot.snapshot_id}", "dataset_id",
        lambda: client.find_dataset(ds_name),
        lambda: client.create_dataset(ds_name, dataset_rows(snapshot, manifest.case_ids)),
    )
    if dataset_id is None:
        return summary

    ex_name = experiment_name(manifest.run_id)
    _upload_one(
        store, summary, f"experiment:{manifest.run_id}", "experiment_id",
        lambda: client.find_experiment(dataset_id, ex_name),
        lambda: client.create_experiment(dataset_id, ex_name, experiment_rows(store)),
    )
    return summary
