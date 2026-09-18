"""Durable, append-only evaluation artifacts with checkpoint/resume.

AX plan: "Persist candidate outputs before judging, and each judge result
before uploading. Maintain explicit generated/scored/upload-pending/
confirmed/error states with attempt identity." These files ARE the
reproducibility record; AX holds derived copies subject to its retention.

Layout under ``<artifacts_root>/<run_id>/``::

    run.json        immutable run manifest (dataset, producer, judge config)
    outputs.jsonl   frozen candidate outputs, one per (case, repetition)
    results.jsonl   judge results, one record per attempt at a unit
    uploads.jsonl   AX upload state transitions

Every append writes one canonical-JSON line, flushes and fsyncs before
returning, so an interruption loses at most the unit in flight. Nothing
is ever rewritten: re-appending an identical record is a no-op (this is
what makes resume idempotent) and a *different* record for a unit that
already has a terminal one raises ArtifactConflictError.

Artifacts contain synthetic/public evaluation content and judge feedback.
Choose a directory outside the repository (or otherwise excluded from
commits); never point this at canonical student data.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from eval_results import CriterionResult, ErrorCode, Outcome, UnitKey, canonical_json, sha256_hex


class ArtifactConflictError(Exception):
    """A record would contradict an already persisted one."""


class JudgeConfig(BaseModel):
    """Everything about the judge that can change a judgement."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model_id: str
    model_revision: str
    tokenizer_revision: str
    template_version: str
    host: Literal["modal", "lightning", "kaggle", "fixture"]
    gpu: str
    dtype: str
    image_digest: str
    inference_library: str
    max_total_tokens: int = Field(ge=1)
    max_new_tokens: int = Field(ge=1)
    temperature: float = Field(ge=0)
    seed: Optional[int] = None

    @property
    def judge_config_id(self) -> str:
        return "judge-" + sha256_hex(canonical_json(self.model_dump(mode="json")))[:16]


class ProducerConfig(BaseModel):
    """What produced the candidate outputs (the thing being compared)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str
    source: Literal["netra", "fixture_replay"]
    commit: str
    tutor_model: Optional[str] = None
    prompt_version: Optional[str] = None
    notes: Optional[str] = None
    intended_changes: list[str] = Field(default_factory=list)
    confounding_changes: list[str] = Field(default_factory=list)


class RunManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    dataset_name: str
    dataset_hash: str
    split: Literal["development", "calibration", "heldout"]
    case_ids: list[str]
    repetitions: int = Field(ge=1)
    criteria: list[str]
    rubric_hashes: dict[str, str]
    """evaluation_id -> hash of the rubric file used; part of comparison identity."""
    producer: ProducerConfig
    judge: JudgeConfig
    created_at: datetime


class FrozenOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    case_id: str
    repetition: int = Field(ge=0)
    response: str
    response_hash: str
    trace_id: Optional[str] = None
    """The producer turn's trace id, for trace-to-case reconciliation."""
    generated_at: datetime


class ResultRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    attempt: int = Field(ge=1)
    result: CriterionResult
    recorded_at: datetime


UploadState = Literal["pending", "confirmed", "ambiguous", "error"]


class UploadRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    object_key: str
    """What was uploaded: ``dataset:<snapshot>``, ``experiment:<run_id>``
    or ``result:<unit key>``."""
    state: UploadState
    external_ids: dict[str, str] = Field(default_factory=dict)
    error_code: Optional[str] = None
    attempt: int = Field(ge=1)
    recorded_at: datetime


RETRYABLE_MISSING = frozenset({ErrorCode.BUDGET_EXHAUSTED, ErrorCode.RUN_STOPPED})


def is_terminal(result: CriterionResult) -> bool:
    """A terminal result is never superseded by a later attempt.

    FAILED and budget/stop MISSING results may be retried by a later run
    attempt (after reconciliation for uncertain completion). SCORED,
    INVALID, NOT_APPLICABLE and reference-pending MISSING are final for
    this run; re-judging an invalid output is a new, explicit run.
    """

    if result.outcome is Outcome.FAILED:
        # Oversized input is deterministic: retrying spends allowance for
        # the same refusal. It needs a reviewed shorter case (a new dataset
        # version), never silent truncation.
        return result.error_code is ErrorCode.OVERSIZED_INPUT
    if result.outcome is Outcome.MISSING and result.error_code in RETRYABLE_MISSING:
        return False
    return True


def _now() -> datetime:
    return datetime.now(timezone.utc)


class RunStore:
    def __init__(self, artifacts_root: Path, run_id: str) -> None:
        self.run_dir = artifacts_root / run_id
        self.run_id = run_id

    # --- low-level append-only JSONL -------------------------------------

    def _path(self, name: str) -> Path:
        return self.run_dir / name

    def _append(self, name: str, record: BaseModel) -> None:
        line = canonical_json(record.model_dump(mode="json")) + "\n"
        with self._path(name).open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())

    def _read(self, name: str) -> Iterator[dict[str, Any]]:
        path = self._path(name)
        if not path.exists():
            return
        with path.open("r", encoding="utf-8") as handle:
            for number, line in enumerate(handle, start=1):
                if not line.endswith("\n"):
                    # A torn final line from an interrupted append: the unit
                    # it described was not persisted, so it is redone.
                    break
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as error:
                    raise ArtifactConflictError(f"{name}:{number} is corrupt") from error

    # --- manifest ----------------------------------------------------------

    def create(self, manifest: RunManifest) -> RunManifest:
        """Write run.json once. An identical manifest is accepted (resume);
        a different one for the same run_id is refused."""

        if manifest.run_id != self.run_id:
            raise ArtifactConflictError("manifest run_id does not match the store")
        self.run_dir.mkdir(parents=True, exist_ok=True)
        path = self._path("run.json")
        if path.exists():
            existing = self.manifest()
            if existing.model_dump(exclude={"created_at"}) != manifest.model_dump(exclude={"created_at"}):
                raise ArtifactConflictError(f"run {self.run_id} already exists with a different manifest")
            return existing
        with path.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return manifest

    def manifest(self) -> RunManifest:
        return RunManifest.model_validate(json.loads(self._path("run.json").read_text(encoding="utf-8")))

    # --- outputs ---------------------------------------------------------------

    def outputs(self) -> dict[tuple[str, int], FrozenOutput]:
        found: dict[tuple[str, int], FrozenOutput] = {}
        for raw in self._read("outputs.jsonl"):
            output = FrozenOutput.model_validate(raw)
            found.setdefault((output.case_id, output.repetition), output)
        return found

    def append_output(self, case_id: str, repetition: int, response: str, trace_id: Optional[str] = None) -> FrozenOutput:
        existing = self.outputs().get((case_id, repetition))
        response_hash = sha256_hex(response)
        if existing is not None:
            if existing.response_hash != response_hash:
                raise ArtifactConflictError(f"output for {case_id} r{repetition} is already frozen")
            return existing
        output = FrozenOutput(
            run_id=self.run_id,
            case_id=case_id,
            repetition=repetition,
            response=response,
            response_hash=response_hash,
            trace_id=trace_id,
            generated_at=_now(),
        )
        self._append("outputs.jsonl", output)
        return output

    # --- results ---------------------------------------------------------------

    def result_history(self) -> dict[UnitKey, list[ResultRecord]]:
        history: dict[UnitKey, list[ResultRecord]] = {}
        for raw in self._read("results.jsonl"):
            record = ResultRecord.model_validate(raw)
            history.setdefault(record.result.key, []).append(record)
        return history

    def effective_results(self) -> dict[UnitKey, CriterionResult]:
        return {key: records[-1].result for key, records in self.result_history().items()}

    def append_result(self, result: CriterionResult) -> ResultRecord:
        if result.key.run_id != self.run_id:
            raise ArtifactConflictError("result belongs to a different run")
        records = self.result_history().get(result.key, [])
        if records:
            latest = records[-1]
            if latest.result == result:
                return latest
            if is_terminal(latest.result):
                raise ArtifactConflictError(f"{result.key.as_str()} already has a terminal result")
        record = ResultRecord(attempt=len(records) + 1, result=result, recorded_at=_now())
        self._append("results.jsonl", record)
        return record

    # --- uploads ---------------------------------------------------------------

    def upload_states(self) -> dict[str, UploadRecord]:
        latest: dict[str, UploadRecord] = {}
        for raw in self._read("uploads.jsonl"):
            record = UploadRecord.model_validate(raw)
            latest[record.object_key] = record
        return latest

    def record_upload(
        self,
        object_key: str,
        state: UploadState,
        external_ids: Optional[dict[str, str]] = None,
        error_code: Optional[str] = None,
    ) -> UploadRecord:
        previous = self.upload_states().get(object_key)
        if previous is not None and previous.state == "confirmed":
            if state == "confirmed" and (external_ids or {}) == previous.external_ids:
                return previous
            raise ArtifactConflictError(f"{object_key} is already confirmed")
        record = UploadRecord(
            object_key=object_key,
            state=state,
            external_ids=external_ids or {},
            error_code=error_code,
            attempt=(previous.attempt + 1) if previous else 1,
            recorded_at=_now(),
        )
        self._append("uploads.jsonl", record)
        return record
