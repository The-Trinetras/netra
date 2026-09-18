"""Worker job persistence and execution contracts.

PostgreSQL is the durable store of job state (CLAUDE.md "Background
jobs"). Workers claim jobs via short transactions using lease/claim
semantics (see netra_worker.runtime.leases.Lease) so a later
implementation can adopt `SELECT ... FOR UPDATE SKIP LOCKED` without
changing this interface. Handlers must be idempotent: at-least-once
execution is assumed, and a claim transaction must never stay open
while a handler awaits an external provider.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Protocol, TypeVar
from uuid import UUID

from pydantic import BaseModel, Field

from netra_worker.runtime.leases import Lease


class JobStatus(str, Enum):
    PENDING = "pending"
    LEASED = "leased"
    COMPLETED = "completed"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"
    CANCELLED = "cancelled"
    """Terminal: deliberately cancelled (netra_worker.runtime.errors.JobCancelled)."""


class JobPayload(BaseModel):
    """Base shape every job payload extends."""

    idempotency_key: str


class Job(BaseModel):
    """One durable unit of background work."""

    job_id: UUID
    job_type: str
    payload: Dict[str, Any]
    status: JobStatus = JobStatus.PENDING
    attempt_count: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=5, ge=1)
    next_run_at: datetime
    lease: Optional[Lease] = None
    created_at: datetime
    updated_at: datetime

    completed_stages: List[str] = Field(default_factory=list)
    """Stages of this job already known to have finished.

    A retry skips these rather than redoing them. Without it a job that
    crashed midway restarts from the beginning, which is only safe when
    every stage is individually idempotent."""

    remote_operation_ids: Dict[str, str] = Field(default_factory=dict)
    """Identifiers returned by external providers, keyed by stage.

    backend-data.md: "Record completed stages and remote operation IDs
    for safe recovery" and "Do not blindly repeat an external call after
    an uncertain completion."

    This is what makes a crash between an external effect and its
    acknowledgement recoverable. A video indexed with a provider, for
    instance, returns an asset id; if the worker dies before recording
    it, a retry has no way to tell that the asset already exists and
    creates a duplicate. Storing the id lets the retry reconcile instead
    of re-calling."""


TPayload = TypeVar("TPayload", bound=JobPayload, contravariant=True)


class JobHandler(Protocol[TPayload]):
    """Executes one claimed job. Must be safe to re-run (at-least-once execution)."""

    async def handle(self, payload: TPayload) -> None:
        ...


class JobRepository(Protocol):
    """Typed contract for enqueueing and claiming jobs.

    A concrete PostgreSQL implementation keeps claim transactions short
    (CLAUDE.md "Job claim design must allow short transactions and SKIP
    LOCKED later") and never holds a transaction open across an
    external provider call.
    """

    def enqueue(self, job_type: str, payload: JobPayload, idempotency_key: str) -> Job:
        """Idempotent by idempotency_key: re-enqueuing an already-known key
        returns the existing Job rather than duplicating it."""
        ...

    def claim_next(
        self, job_types: List[str], worker_id: str, lease_duration_seconds: int
    ) -> Optional[Job]:
        """Atomically claim one pending/due job of job_types, if any, for worker_id."""
        ...

    def complete(self, job_id: UUID, lease: Lease) -> None:
        ...

    def fail(self, job_id: UUID, lease: Lease, next_run_at: datetime, retryable: bool = True) -> None:
        """Record a failed attempt and reschedule per the retry/backoff policy
        (see netra_worker.runtime.retries)."""
        ...

    def heartbeat(self, job_id: UUID, lease: Lease, new_expires_at: datetime) -> Lease:
        """Extend a lease for a long-running job."""
        ...

    def cancel(self, job_id: UUID, lease: Lease) -> None:
        """Record the job as cancelled (terminal, not retried). Fenced by the
        lease like complete/fail."""
        ...

    def record_stage(
        self,
        job_id: UUID,
        lease: Lease,
        stage: str,
        remote_operation_id: Optional[str] = None,
    ) -> Job:
        """Durably mark one stage complete, with any provider id it produced.

        Must be committed on its own, immediately after the external
        effect it describes, and before the next stage starts. Batching
        these until the job finishes defeats the purpose: the window this
        closes is precisely a crash partway through.

        Implementations must reject the write when lease is no longer the
        job's current lease, so a worker whose lease expired cannot
        record progress as the current owner."""
        ...
