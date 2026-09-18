"""Bounded, PostgreSQL-coordinated worker pools.

Each claimed job runs as one *attempt*: the handler and a lease-renewal task
race, and the attempt ends in exactly one of completed / retry scheduled /
dead-lettered / lease lost. Repository writes are fenced by the lease, so a
worker that lost ownership cannot complete or fail the job as its owner.

Resilience rules:
- A database error while claiming, completing or failing never terminates the
  worker loop; the worker backs off and polls again. At-least-once delivery
  means an unrecorded outcome is simply retried after the lease expires.
- Invalid payloads (``pydantic.ValidationError``) and ``PermanentJobError``
  are dead-lettered, never retried.
- Tracing uses M1's injected ``Tracer``: one span per attempt, never a span
  held open while a job waits in the queue. Telemetry failure cannot change
  job outcomes.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

from pydantic import ValidationError

from netra_api.content.telemetry import bind_context, increment, log_event, observe
from netra_api.platform.tracing import DISABLED_TRACER, Tracer
from netra_worker.runtime.errors import LeaseLostError, PermanentJobError
from netra_worker.runtime.job_repository import Job, JobRepository
from netra_worker.runtime.retries import BackoffPolicy, ExponentialBackoffWithJitter

logger = logging.getLogger(__name__)

JobHandler = Callable[[Job], Awaitable[None]]

__all__ = ["JobHandler", "LeaseLostError", "PermanentJobError", "WorkerPool", "WorkerPoolConfig"]


@dataclass(frozen=True)
class WorkerPoolConfig:
    worker_id: str
    job_types: tuple[str, ...]
    concurrency: int = 1
    lease_duration_seconds: int = 60
    poll_interval_seconds: float = 1.0
    repository_error_backoff_seconds: float = 5.0

    def __post_init__(self) -> None:
        if (self.concurrency < 1 or self.lease_duration_seconds < 1 or self.poll_interval_seconds <= 0
                or self.repository_error_backoff_seconds <= 0):
            raise ValueError("worker pool limits must be positive")
        if not self.job_types:
            raise ValueError("worker pool must handle at least one job type")


class WorkerPool:
    """Run a fixed number of lease-owning workers against one jobs table."""

    def __init__(self, repository: JobRepository, handlers: dict[str, JobHandler],
                 config: WorkerPoolConfig, backoff: BackoffPolicy | None = None,
                 tracer: Tracer | None = None) -> None:
        unknown = set(config.job_types) - set(handlers)
        if unknown:
            raise ValueError(f"missing handlers for job types: {sorted(unknown)}")
        self.repository, self.handlers, self.config = repository, handlers, config
        self.backoff = backoff or ExponentialBackoffWithJitter()
        self.tracer = tracer or DISABLED_TRACER
        self._stopping = asyncio.Event()

    async def run(self, stop_event: asyncio.Event | None = None) -> None:
        stop = stop_event or self._stopping
        workers = [asyncio.create_task(self._worker_loop(f"{self.config.worker_id}-{i}", stop))
                   for i in range(self.config.concurrency)]
        try:
            await asyncio.gather(*workers)
        except asyncio.CancelledError:
            stop.set()
            for worker in workers:
                worker.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
            raise

    def stop(self) -> None:
        self._stopping.set()

    @staticmethod
    async def _sleep_unless_stopped(stop: asyncio.Event, seconds: float) -> None:
        try:
            await asyncio.wait_for(stop.wait(), seconds)
        except asyncio.TimeoutError:
            pass

    async def _worker_loop(self, worker_id: str, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                job = await self.repository.claim_next(list(self.config.job_types), worker_id,
                                                        self.config.lease_duration_seconds)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                increment("netra_jobs_claim_errors_total")
                log_event(logger, "job_claim_failed", component="worker", worker_id=worker_id,
                          error_type=type(exc).__name__, level=logging.WARNING)
                await self._sleep_unless_stopped(stop, self.config.repository_error_backoff_seconds)
                continue
            if job is None:
                await self._sleep_unless_stopped(stop, self.config.poll_interval_seconds)
                continue
            increment("netra_jobs_claimed_total", job_type=job.job_type, status="claimed")
            log_event(logger, "job_claimed", component="worker",
                      job_id=job.job_id, job_type=job.job_type, attempt=job.attempt_count)
            await self._execute(job)

    async def _execute(self, job: Job) -> None:
        if job.lease is None:
            return
        started = time.perf_counter()
        outcome = "error"
        with bind_context(job_id=job.job_id, job_type=job.job_type), self.tracer.span(
                "worker.job_attempt", **{"netra.operation": job.job_type,
                                         "netra.attempt": job.attempt_count}) as span:
            try:
                outcome = await self._run_attempt(job)
            finally:
                span.set(**{"netra.outcome": outcome})
                observe("netra_job_duration_seconds", time.perf_counter() - started, job_type=job.job_type)
                increment("netra_job_attempts_total", job_type=job.job_type, status=outcome)

    async def _run_attempt(self, job: Job) -> str:
        assert job.lease is not None
        handler_task = asyncio.create_task(self.handlers[job.job_type](job))
        renewal_task = asyncio.create_task(self._renew(job))
        try:
            done, _ = await asyncio.wait({handler_task, renewal_task}, return_when=asyncio.FIRST_COMPLETED)
            if handler_task not in done:
                # Renewal ended first: ownership is gone (or unknowable). Stop the
                # handler; the next claimant re-runs it after lease expiry.
                handler_task.cancel()
                await asyncio.gather(handler_task, return_exceptions=True)
                log_event(logger, "job_lease_lost", component="worker", job_id=job.job_id,
                          job_type=job.job_type, level=logging.WARNING)
                return "lease_lost"
            error = handler_task.exception()
        except asyncio.CancelledError:
            handler_task.cancel()
            await asyncio.gather(handler_task, return_exceptions=True)
            raise
        finally:
            if not renewal_task.done():
                renewal_task.cancel()
            await asyncio.gather(renewal_task, return_exceptions=True)

        if error is None:
            return await self._record(job, "completed", lambda: self.repository.complete(job.job_id, job.lease))
        if isinstance(error, LeaseLostError):
            return "lease_lost"
        if isinstance(error, (PermanentJobError, ValidationError)):
            log_event(logger, "job_dead_lettered", component="worker", job_id=job.job_id,
                      job_type=job.job_type, error_type=type(error).__name__, level=logging.ERROR)
            return await self._record(job, "dead_lettered", lambda: self.repository.fail(
                job.job_id, job.lease, datetime.now(timezone.utc), retryable=False))
        delay = self.backoff.next_delay(job.attempt_count)
        log_event(logger, "job_retry_scheduled", component="worker", job_id=job.job_id,
                  job_type=job.job_type, error_type=type(error).__name__,
                  delay_seconds=round(delay.total_seconds(), 3))
        return await self._record(job, "retry_scheduled", lambda: self.repository.fail(
            job.job_id, job.lease, datetime.now(timezone.utc) + delay, retryable=True))

    async def _record(self, job: Job, outcome: str, write: Callable[[], Awaitable[None]]) -> str:
        """Persist the attempt outcome; a fenced or failed write never kills the loop."""

        try:
            await write()
        except LeaseLostError:
            log_event(logger, "job_outcome_fenced", component="worker", job_id=job.job_id,
                      job_type=job.job_type, outcome=outcome, level=logging.WARNING)
            return "lease_lost"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Unrecorded outcome: the lease expires and the job is re-claimed.
            # Handlers are idempotent, so this costs a repeat, not a duplicate effect.
            log_event(logger, "job_outcome_unrecorded", component="worker", job_id=job.job_id,
                      job_type=job.job_type, outcome=outcome, error_type=type(exc).__name__,
                      level=logging.ERROR)
            return "outcome_unrecorded"
        log_event(logger, f"job_{outcome}", component="worker", job_id=job.job_id, job_type=job.job_type)
        return outcome

    async def _renew(self, job: Job) -> None:
        """Extend the lease until cancelled; return only when ownership is lost.

        A transient repository error is retried while the current lease is
        still valid. Once it has expired (or the repository reports a lost
        lease) the worker must assume another claimant may own the job.
        """

        assert job.lease is not None
        interval = max(0.1, self.config.lease_duration_seconds / 3)
        while True:
            await asyncio.sleep(interval)
            expires = datetime.now(timezone.utc) + timedelta(seconds=self.config.lease_duration_seconds)
            try:
                job.lease = await self.repository.heartbeat(job.job_id, job.lease, expires)
            except LeaseLostError:
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log_event(logger, "job_heartbeat_failed", component="worker", job_id=job.job_id,
                          error_type=type(exc).__name__, level=logging.WARNING)
                if datetime.now(timezone.utc) + timedelta(seconds=interval) >= job.lease.expires_at:
                    return
