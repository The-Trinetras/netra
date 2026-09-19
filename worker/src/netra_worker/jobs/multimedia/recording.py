"""Bind multimedia jobs to M2's JobRepository: stage recording and lease-aware cancellation.

``RepositoryStageRecorder`` is the StageRecorder a real claimed job uses.
It delegates every write to ``JobRepository.record_stage`` for the job
and lease it was built with — one commit per stage, immediately after
the effect — and rebuilds its view of completed stages and remote ids
from the Job the repository returns, never from its own guess. The
repository is the lease authority: M2's implementation must reject a
write on a lost lease (job_repository.py), and that rejection propagates
unchanged so the job stops as a lost owner rather than a failure.

``LeaseCancellationToken`` stops a job before its next stage when the
lease has expired (minus an explicit safety margin), or when an explicit
cancellation source says so. Continuing to call providers on work
another worker now owns is the lease-loss mistake one step earlier.

``LeaseHolder`` is shared by both so a heartbeat renewal is seen by the
recorder and the token at once.

This is written against the JobRepository Protocol on main. M2's real
PostgreSQL implementation is unpublished; its acceptance is the contract
suite in worker/tests/multimedia/contracts.py (INT-04).
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from netra_worker.jobs.multimedia.base import CancellationToken
from netra_worker.runtime.job_repository import Job, JobRepository
from netra_worker.runtime.leases import Lease


class LeaseHolder:
    """The current lease for one claimed job, updated on heartbeat."""

    def __init__(self, lease: Lease) -> None:
        self._lease = lease

    @classmethod
    def tracking(cls, job: Job) -> "LeaseHolder":
        """A holder that always reads the job's live lease.

        The worker dispatcher renews a claimed job by replacing ``job.lease``
        on the very Job object it hands the handler. A holder built from a
        copy of the lease would keep the ORIGINAL expiry, so a long job's
        LeaseCancellationToken would stop it at the first expiry despite
        successful renewals. Composition therefore builds holders with this.
        """

        if job.lease is None:
            raise ValueError("a job without a lease cannot be tracked")
        return _JobLeaseHolder(job)

    @property
    def lease(self) -> Lease:
        return self._lease

    def renew(self, lease: Lease) -> None:
        if lease.token != self._lease.token:
            raise ValueError("a renewal must keep the lease token; a new token is a new claim")
        self._lease = lease


class _JobLeaseHolder(LeaseHolder):
    def __init__(self, job: Job) -> None:
        super().__init__(job.lease)
        self._job = job

    @property
    def lease(self) -> Lease:
        current = self._job.lease
        if current is None or current.token != self._lease.token:
            # The dispatcher never swaps tokens mid-attempt; a different token
            # would mean a new claim, which this holder must not adopt.
            return self._lease
        return current

    def renew(self, lease: Lease) -> None:
        super().renew(lease)
        self._job.lease = lease


class RepositoryStageRecorder:
    """StageRecorder over JobRepository.record_stage for one claimed job."""

    def __init__(self, repository: JobRepository, job: Job, holder: LeaseHolder) -> None:
        if job.lease is None or job.lease.token != holder.lease.token:
            raise ValueError("the recorder must be built for the job's current lease")
        self._repository = repository
        self._job_id = job.job_id
        self._holder = holder
        self._stages: List[str] = list(job.completed_stages)
        self._remote_ids: Dict[str, str] = dict(job.remote_operation_ids)

    async def completed_stages(self) -> List[str]:
        return list(self._stages)

    async def remote_operation_id(self, stage: str) -> Optional[str]:
        return self._remote_ids.get(stage)

    async def record(self, stage: str, remote_operation_id: Optional[str] = None) -> None:
        result: Any = self._repository.record_stage(self._job_id, self._holder.lease, stage, remote_operation_id)
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, Job):
            raise TypeError("JobRepository.record_stage must return the updated Job")
        if stage not in result.completed_stages:
            raise RuntimeError(f"repository did not record stage {stage}")
        if remote_operation_id is not None and result.remote_operation_ids.get(stage) != remote_operation_id:
            raise RuntimeError(f"repository did not keep the remote operation id for stage {stage}")
        self._stages = list(result.completed_stages)
        self._remote_ids = dict(result.remote_operation_ids)


class LeaseCancellationToken:
    """CancellationToken: lease expiry (with margin) or an explicit source."""

    def __init__(
        self,
        holder: LeaseHolder,
        *,
        safety_margin: timedelta,
        explicit: Optional[CancellationToken] = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        if safety_margin < timedelta(0):
            raise ValueError("safety_margin must not be negative")
        self._holder = holder
        self._margin = safety_margin
        self._explicit = explicit
        self._clock = clock

    def is_cancelled(self) -> bool:
        if self._explicit is not None and self._explicit.is_cancelled():
            return True
        return self._clock() >= self._holder.lease.expires_at - self._margin

    def lease_expired(self) -> bool:
        """True when the stop is due to the lease, not an explicit cancellation.

        run_stages raises LeaseLostError for this case (the next claimant
        resumes the job) and JobCancelledError only for explicit cancellation.
        """

        explicit = self._explicit is not None and self._explicit.is_cancelled()
        return not explicit and self._clock() >= self._holder.lease.expires_at - self._margin

    def reason(self) -> str:
        if self._explicit is not None and self._explicit.is_cancelled():
            return self._explicit.reason()
        return "lease expired or within its safety margin"
