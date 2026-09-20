"""C5 job contract: schema <-> Python mirror, examples and status derivation."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from netra_api.content.job_status import (
    FAILURE_TEXT,
    FailureReason,
    JobFailure,
    JobKind,
    JobStage,
    JobState,
    JobStatus,
    StageJobState,
    derive_job_status,
)
from netra_api.content.sources.models import SourceVersion, SourceVersionIngestionState as S, SourceVersionStatus as V

REPO = Path(__file__).resolve().parents[3]
SCHEMA = json.loads((REPO / "shared" / "contracts" / "jobs" / "v1" / "job.schema.json").read_text(encoding="utf-8"))
EXAMPLES = REPO / "shared" / "contracts" / "examples" / "jobs"
T0 = datetime(2026, 9, 20, 9, 15, tzinfo=timezone.utc)


def _enum(path: list[str]) -> set[str]:
    node = SCHEMA
    for key in path:
        node = node[key]
    return set(node["enum"])


def test_schema_enums_match_the_python_mirror():
    assert _enum(["properties", "kind"]) == {k.value for k in JobKind}
    assert _enum(["properties", "state"]) == {s.value for s in JobState}
    assert _enum(["properties", "stage"]) == {s.value for s in JobStage}
    assert _enum(["$defs", "JobFailure", "properties", "reason"]) == {r.value for r in FailureReason}


def test_schema_fields_and_required_match_the_python_mirror():
    assert set(SCHEMA["properties"]) == set(JobStatus.model_fields)
    required = {name for name, field in JobStatus.model_fields.items() if field.is_required()}
    assert set(SCHEMA["required"]) == required
    assert set(SCHEMA["$defs"]["JobFailure"]["properties"]) == set(JobFailure.model_fields)
    assert SCHEMA["additionalProperties"] is False


@pytest.mark.parametrize("name", ["job_processing", "job_ready", "job_failed"])
def test_examples_are_valid_and_round_trip(name):
    body = json.loads((EXAMPLES / f"{name}.json").read_text(encoding="utf-8"))
    job = JobStatus.model_validate(body["job"])
    assert job.public() == body["job"]


def test_list_example_is_valid():
    body = json.loads((EXAMPLES / "job_list.json").read_text(encoding="utf-8"))
    assert [JobStatus.model_validate(item).state for item in body["jobs"]] == [JobState.PROCESSING]


def test_failed_example_uses_the_servers_own_wording():
    body = json.loads((EXAMPLES / "job_failed.json").read_text(encoding="utf-8"))
    failure = JobStatus.model_validate(body["job"]).failure
    assert (failure.message, failure.retryable) == FAILURE_TEXT[failure.reason]


def _base(**overrides):
    values = dict(job_id=uuid4(), kind="document_ingestion", title="t", created_at=T0, updated_at=T0,
                  source_id=uuid4(), source_version_id=uuid4())
    values.update(overrides)
    return values


@pytest.mark.parametrize("values", [
    {"state": "failed"},                                                       # failed without failure
    {"state": "processing", "failure": JobFailure.for_reason(FailureReason.PROCESSING_FAILED)},
    {"state": "ready", "stage": "indexing"},                                   # stage only while processing
    {"state": "ready", "poll_after_ms": 2000},
    {"state": "ready", "source_version_id": None},                             # ready must be openable
    {"state": "failed", "failure": JobFailure.for_reason(FailureReason.PROCESSING_FAILED), "stage": "queued"},
    {"state": "processing", "poll_after_ms": 10},                              # below the schema minimum
    {"state": "processing", "extra": 1},                                       # additionalProperties false
])
def test_schema_conditional_rules_are_enforced(values):
    with pytest.raises(ValidationError):
        JobStatus(**_base(**values))


def test_every_failure_reason_has_safe_bounded_wording():
    assert set(FAILURE_TEXT) == set(FailureReason)
    assert all(0 < len(message) <= 300 for message, _ in FAILURE_TEXT.values())
    assert FAILURE_TEXT[FailureReason.UNREADABLE_DOCUMENT][1] is False


# --------------------------------------------------------------------------
# Derivation from canonical pipeline state
# --------------------------------------------------------------------------


def _version(state=S.PENDING, status=V.PENDING, active=False):
    return SourceVersion(source_version_id=uuid4(), source_id=uuid4(), version_number=1, created_at=T0,
                         ingestion_state=state, status=status, is_active=active)


def _job(job_type="parse_document", status="pending", attempts=0, minutes=0):
    return StageJobState(job_type=job_type, status=status, attempts=attempts,
                         updated_at=T0 + timedelta(minutes=minutes))


def _derive(version, jobs=(), **kwargs):
    return derive_job_status(job_id=uuid4(), title="Chapter 4", version=version, stage_jobs=jobs,
                             created_at=T0, **kwargs)


@pytest.mark.parametrize("state,status,jobs,expected", [
    (S.PENDING, V.PENDING, [_job()], JobStage.QUEUED),
    (S.PENDING, V.PENDING, [_job(status="leased", attempts=1)], JobStage.READING),
    (S.PENDING, V.PENDING, [_job(status="pending", attempts=1)], JobStage.READING),  # waiting to retry
    (S.PARSING, V.PROCESSING, [], JobStage.STRUCTURING),
    (S.BLOCKS_BUILT, V.PROCESSING, [], JobStage.INDEXING),
    (S.PROJECTED, V.PROCESSING, [], JobStage.INDEXING),
    (S.READY, V.READY, [], JobStage.ACTIVATING),  # finished but not yet openable
])
def test_processing_stages(state, status, jobs, expected):
    job = _derive(_version(state, status), jobs)
    assert (job.state, job.stage, job.poll_after_ms) == (JobState.PROCESSING, expected, 2000)


def test_ready_only_when_the_version_is_active_and_openable():
    version = _version(S.ACTIVE, V.READY, active=True)
    job = _derive(version)
    assert job.state is JobState.READY and job.stage is None and job.poll_after_ms is None
    assert (job.source_id, job.source_version_id) == (version.source_id, version.source_version_id)


def test_failed_version_reports_the_recorded_reason():
    job = _derive(_version(S.FAILED, V.FAILED), failure_reason=FailureReason.UNREADABLE_DOCUMENT)
    assert job.state is JobState.FAILED
    assert job.failure == JobFailure.for_reason(FailureReason.UNREADABLE_DOCUMENT)


def test_failed_without_a_recorded_reason_is_processing_failed():
    job = _derive(_version(S.FAILED, V.FAILED))
    assert job.failure.reason is FailureReason.PROCESSING_FAILED


def test_a_dead_lettered_stage_fails_the_upload_even_if_the_version_looks_healthy():
    job = _derive(_version(S.READY, V.READY), [_job("activate_version", status="dead_letter", attempts=5)])
    assert job.state is JobState.FAILED and job.failure.reason is FailureReason.PROCESSING_FAILED


def test_updated_at_is_the_latest_stage_activity():
    job = _derive(_version(S.PARSING, V.PROCESSING), [_job(minutes=1), _job("build_blocks", minutes=3)])
    assert job.updated_at == T0 + timedelta(minutes=3)
