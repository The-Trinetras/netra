"""Shared payload and staging mechanics for multimedia background jobs.

Figure/diagram/equation/table/video processing are explicitly-listed
NON-agent bounded workflows (CLAUDE.md "Architecture invariants"). Each
job below is a netra_worker.runtime.job_repository.JobHandler:
idempotent, and safe to re-run under at-least-once execution.

This module holds what all of them share: the payload fields, and the
staging loop that makes "safe to re-run" true rather than aspirational.

Why a staging loop at all. backend-data.md requires recording "completed
stages and remote operation IDs for safe recovery" and forbids blindly
repeating an external call after an uncertain completion. A handler
written as a straight line of awaits cannot honour that: if it crashes
after indexing a video and before writing the result, the retry indexes
it again and the account pays twice for a duplicate asset. run_stages
turns the handler into a sequence of individually recorded steps, so a
retry resumes instead of restarting.

Why the ports are declared here. The worker imports nothing from
netra_api at runtime — that is what keeps the two processes separately
deployable (see worker/pyproject.toml) — so these Protocols describe
what a job needs without reaching across that line. M3 owns the handlers
and these ports; M2 owns the PostgreSQL implementations behind them.
The concrete implementations are recorded as a pending boundary in
docs/team/handoffs/M3.md rather than written here.
"""

from __future__ import annotations

from typing import Awaitable, Callable, List, Optional, Protocol
from uuid import UUID

from netra_worker.runtime.job_repository import JobPayload


class MultimediaJobPayload(JobPayload):
    """Fields every multimedia processing job payload carries.

    source_id/source_version_id mirror
    netra_worker.jobs.ingestion.base.IngestionJobPayload — multimedia
    jobs only ever run against an already-ingested, specific
    SourceVersion, never a bare Source (CLAUDE.md "do not bypass
    source-version checks").
    """

    source_id: UUID
    source_version_id: UUID


class JobCancelledError(Exception):
    """Raised when a job stops because it was cancelled or lost its lease.

    Distinct from a failure. A cancelled job has not gone wrong: the
    student pressed STOP, the turn was superseded, or another worker now
    owns the lease. Nothing it produced afterwards may be delivered
    (current-scope.md: "Cancelled or disconnected generations cannot
    resume audio"), and it must not be rescheduled as a failed attempt.
    """

    def __init__(self, stage: str, reason: str) -> None:
        self.stage = stage
        self.reason = reason
        super().__init__(f"cancelled before stage {stage}: {reason}")


class JobDeadlineExceededError(Exception):
    """Raised when no time remains to start the next stage.

    Checked before a stage rather than after, because the point is to
    avoid starting external work whose result nobody will wait for. A
    stage already in flight is allowed to finish and be recorded; losing
    a completed provider call to a deadline is how duplicates are
    created.
    """

    def __init__(self, stage: str) -> None:
        self.stage = stage
        super().__init__(f"deadline exceeded before stage {stage}")


class CancellationToken(Protocol):
    """Whether this unit of work should still be running.

    Implementations combine the sources that can stop a job: an explicit
    cancellation request, a superseded turn, and lease loss. Lease loss
    belongs here because a worker whose lease expired "must not commit as
    the current owner" (backend-data.md) — continuing to call providers
    on work somebody else now owns is the same mistake, one step
    earlier.
    """

    def is_cancelled(self) -> bool:
        ...

    def reason(self) -> str:
        """Short operational explanation, recorded with the outcome."""
        ...


class Deadline(Protocol):
    """How much time this unit of work has left."""

    def remaining_seconds(self) -> float:
        ...


class StageRecorder(Protocol):
    """Durably records stage completion and any provider id it produced.

    Mirrors netra_worker.runtime.job_repository.JobRepository.record_stage
    for one claimed job, with the job id and lease already bound. Each
    call commits on its own, immediately after the effect it describes:
    batching them until the job ends would reopen exactly the crash
    window the record exists to close.
    """

    async def completed_stages(self) -> List[str]:
        ...

    async def remote_operation_id(self, stage: str) -> Optional[str]:
        """The provider id recorded for stage, if it completed with one.

        This is what lets a retry reconcile rather than re-call: an
        indexing stage that recorded an asset id does not need to index
        again, even though the worker that did it never got further.
        """
        ...

    async def record(self, stage: str, remote_operation_id: Optional[str] = None) -> None:
        """Mark stage complete. Must reject the write on a lost lease."""
        ...


StageFunc = Callable[[], Awaitable[Optional[str]]]
"""One stage's work. Returns a provider operation id to record, or None.

Returning the id rather than recording it inside the stage keeps the
commit in one place, so no stage can perform an external effect and
forget to make it recoverable.
"""


async def run_stages(
    recorder: StageRecorder,
    stages: List[tuple[str, StageFunc]],
    *,
    cancellation: Optional[CancellationToken] = None,
    deadline: Optional[Deadline] = None,
) -> List[str]:
    """Run stages in order, skipping ones already recorded as complete.

    Returns the names of the stages this call actually executed, which
    is what a test needs to prove that a replay did nothing twice.

    Before each stage: skip it if it is already recorded, stop if the
    work was cancelled, stop if no time remains. After each stage:
    record it, with its provider id when it produced one.

    Cancellation and the deadline are checked between stages only. A
    stage is expected to honour them internally too — a provider call
    passes the remaining time as its own timeout — but interrupting one
    from out here would leave an external effect unrecorded, which is
    worse than finishing it.
    """

    already_done = set(await recorder.completed_stages())
    executed: List[str] = []

    for stage_name, stage_func in stages:
        if stage_name in already_done:
            continue
        if cancellation is not None and cancellation.is_cancelled():
            raise JobCancelledError(stage_name, cancellation.reason())
        if deadline is not None and deadline.remaining_seconds() <= 0:
            raise JobDeadlineExceededError(stage_name)

        remote_operation_id = await stage_func()
        await recorder.record(stage_name, remote_operation_id)
        executed.append(stage_name)

    return executed
