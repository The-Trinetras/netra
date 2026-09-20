"""Public job status (C5, shared/contracts/jobs/v1/job.schema.json) and its derivation.

The client polls one upload's processing and sees exactly three states:
processing, ready or failed. This module is the Python mirror of the schema
(pydantic, ``extra="forbid"``, same conditional rules) plus the single place
that maps M2's canonical pipeline state (source version + its stage jobs)
onto that public shape. It never exposes exception text, provider output,
object keys or another account's identifiers.

``ready`` is deliberately stricter than "ingestion finished": a job is ready
only when its source version is ACTIVE, i.e. listed by the sources route and
openable. A version that finished but lost activation to a newer one, or a
stage job that exhausted its retries, is ``failed`` with reason
``processing_failed``.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Iterable, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from netra_api.content.sources.models import SourceVersion, SourceVersionIngestionState, SourceVersionStatus

MAX_TITLE_CHARS = 200
DEFAULT_POLL_AFTER_MS = 2000
RECENT_JOBS_LIMIT = 20


class JobKind(str, Enum):
    DOCUMENT_INGESTION = "document_ingestion"


class JobState(str, Enum):
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class JobStage(str, Enum):
    QUEUED = "queued"
    READING = "reading"
    STRUCTURING = "structuring"
    INDEXING = "indexing"
    ACTIVATING = "activating"


class FailureReason(str, Enum):
    UNREADABLE_DOCUMENT = "unreadable_document"
    UNSUPPORTED_DOCUMENT = "unsupported_document"
    CONTENT_MISMATCH = "content_mismatch"
    PROCESSING_FAILED = "processing_failed"


# Server-chosen safe wording and retry advice, one row per reason. The client
# may show ``message`` as is; it never contains exception or provider text.
FAILURE_TEXT: dict[FailureReason, tuple[str, bool]] = {
    FailureReason.UNREADABLE_DOCUMENT: (
        "Netra could not read any text in this document, even with text recognition.", False),
    FailureReason.UNSUPPORTED_DOCUMENT: (
        "This file type is not supported. Upload a PDF document.", False),
    FailureReason.CONTENT_MISMATCH: (
        "The stored copy of this document did not match the upload. Please upload it again.", True),
    FailureReason.PROCESSING_FAILED: (
        "Netra could not finish preparing this document. Please try uploading it again later.", True),
}


class JobFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reason: FailureReason
    message: str = Field(max_length=300)
    retryable: bool

    @classmethod
    def for_reason(cls, reason: FailureReason) -> "JobFailure":
        message, retryable = FAILURE_TEXT[reason]
        return cls(reason=reason, message=message, retryable=retryable)


class JobStatus(BaseModel):
    """Mirror of job.schema.json, including its conditional rules."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    job_id: UUID
    kind: JobKind
    state: JobState
    stage: Optional[JobStage] = None
    title: str = Field(min_length=1, max_length=MAX_TITLE_CHARS)
    source_id: Optional[UUID] = None
    source_version_id: Optional[UUID] = None
    failure: Optional[JobFailure] = None
    poll_after_ms: Optional[int] = Field(default=None, ge=250, le=60000)
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def _state_rules(self) -> "JobStatus":
        if self.state is JobState.FAILED:
            if self.failure is None:
                raise ValueError("a failed job carries its failure")
        elif self.failure is not None:
            raise ValueError("only a failed job carries a failure")
        if self.state is not JobState.PROCESSING and (self.stage is not None or self.poll_after_ms is not None):
            raise ValueError("stage and poll_after_ms are only present while processing")
        if self.state is JobState.READY and (self.source_id is None or self.source_version_id is None):
            raise ValueError("a ready job names the source and version to open")
        return self

    def public(self) -> dict:
        """JSON body fragment with absent optional fields omitted, as the schema expects."""

        return self.model_dump(mode="json", exclude_none=True)


class StageJobState(BaseModel):
    """What derivation needs from one pipeline job row (no payload, no errors)."""

    model_config = ConfigDict(frozen=True)

    job_type: str
    status: str
    attempts: int = 0
    updated_at: datetime


_STAGE_BY_STATE = {
    SourceVersionIngestionState.PARSING: JobStage.STRUCTURING,
    SourceVersionIngestionState.BLOCKS_BUILT: JobStage.INDEXING,
    SourceVersionIngestionState.EMBEDDED: JobStage.INDEXING,
    SourceVersionIngestionState.PROJECTED: JobStage.INDEXING,
    SourceVersionIngestionState.READY: JobStage.ACTIVATING,
}


def derive_job_status(
    *,
    job_id: UUID,
    title: str,
    version: SourceVersion,
    stage_jobs: Iterable[StageJobState],
    created_at: datetime,
    failure_reason: Optional[FailureReason] = None,
    poll_after_ms: int = DEFAULT_POLL_AFTER_MS,
) -> JobStatus:
    """Map one upload's canonical pipeline state onto the public job status.

    ``failure_reason`` is the reason recorded when the pipeline failed the
    version; without one a failure is reported as ``processing_failed``.
    """

    jobs = list(stage_jobs)
    updated_at = max([created_at, *(job.updated_at for job in jobs)])
    common = dict(job_id=job_id, kind=JobKind.DOCUMENT_INGESTION, title=title[:MAX_TITLE_CHARS],
                  source_id=version.source_id, source_version_id=version.source_version_id,
                  created_at=created_at, updated_at=updated_at)

    if version.is_active and version.status is SourceVersionStatus.READY:
        return JobStatus(state=JobState.READY, **common)
    dead = any(job.status == "dead_letter" for job in jobs)
    if version.status is SourceVersionStatus.FAILED or dead:
        reason = failure_reason or FailureReason.PROCESSING_FAILED
        return JobStatus(state=JobState.FAILED, failure=JobFailure.for_reason(reason), **common)

    if version.ingestion_state is SourceVersionIngestionState.PENDING:
        parse = [job for job in jobs if job.job_type == "parse_document"]
        started = any(job.status == "leased" or job.attempts > 0 for job in parse)
        stage = JobStage.READING if started else JobStage.QUEUED
    else:
        stage = _STAGE_BY_STATE.get(version.ingestion_state, JobStage.ACTIVATING)
    return JobStatus(state=JobState.PROCESSING, stage=stage, poll_after_ms=poll_after_ms, **common)


__all__ = [
    "DEFAULT_POLL_AFTER_MS",
    "FAILURE_TEXT",
    "FailureReason",
    "JobFailure",
    "JobKind",
    "JobStage",
    "JobState",
    "JobStatus",
    "RECENT_JOBS_LIMIT",
    "StageJobState",
    "derive_job_status",
]
