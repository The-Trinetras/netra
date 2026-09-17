"""Bounded, PostgreSQL-coordinated worker pools."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

from netra_worker.runtime.job_repository import Job, JobRepository
from netra_worker.runtime.retries import BackoffPolicy, ExponentialBackoffWithJitter
from netra_api.platform.observability import bind_context, increment, log_event, observe, start_span
import logging
import time


class PermanentJobError(RuntimeError):
    """A job failure that must be dead-lettered without another attempt."""


class LeaseLostError(RuntimeError):
    """The worker lost ownership while its handler was running."""


JobHandler = Callable[[Job], Awaitable[None]]


@dataclass(frozen=True)
class WorkerPoolConfig:
    worker_id: str
    job_types: tuple[str, ...]
    concurrency: int = 1
    lease_duration_seconds: int = 60
    poll_interval_seconds: float = 1.0

    def __post_init__(self) -> None:
        if self.concurrency < 1 or self.lease_duration_seconds < 1 or self.poll_interval_seconds <= 0:
            raise ValueError("worker pool limits must be positive")
        if not self.job_types:
            raise ValueError("worker pool must handle at least one job type")


class WorkerPool:
    """Run a fixed number of lease-owning workers against one jobs table."""

    def __init__(self, repository: JobRepository, handlers: dict[str, JobHandler],
                 config: WorkerPoolConfig, backoff: BackoffPolicy | None = None) -> None:
        unknown = set(config.job_types) - set(handlers)
        if unknown:
            raise ValueError(f"missing handlers for job types: {sorted(unknown)}")
        self.repository, self.handlers, self.config = repository, handlers, config
        self.backoff = backoff or ExponentialBackoffWithJitter()
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

    async def _worker_loop(self, worker_id: str, stop: asyncio.Event) -> None:
        while not stop.is_set():
            job = await self.repository.claim_next(list(self.config.job_types), worker_id,
                                                    self.config.lease_duration_seconds)
            if job is None:
                try:
                    await asyncio.wait_for(stop.wait(), self.config.poll_interval_seconds)
                except asyncio.TimeoutError:
                    continue
                continue
            increment("netra_jobs_claimed_total", job_type=job.job_type, status="claimed")
            log_event(logging.getLogger(__name__), "job_claimed", component="worker",
                      job_id=job.job_id, job_type=job.job_type,
                      operation_key=job.payload.get("idempotency_key"))
            await self._execute(job)

    async def _execute(self, job: Job) -> None:
        if job.lease is None:
            return
        started = time.perf_counter()
        operation_key = job.payload.get("idempotency_key")
        handler_task = asyncio.create_task(self.handlers[job.job_type](job))
        renewal_task = asyncio.create_task(self._renew(job))
        try:
            with bind_context(job_id=job.job_id, operation_key=operation_key), start_span(
                    "job_execution", component="worker", job_id=job.job_id, job_type=job.job_type):
                done, _ = await asyncio.wait({handler_task, renewal_task}, return_when=asyncio.FIRST_COMPLETED)
                if renewal_task in done:
                    error = renewal_task.exception() or LeaseLostError("job lease was lost")
                    handler_task.cancel()
                    await asyncio.gather(handler_task, return_exceptions=True)
                    raise error
                await handler_task
                await self.repository.complete(job.job_id, job.lease)
            increment("netra_jobs_completed_total", job_type=job.job_type, status="completed")
            log_event(logging.getLogger(__name__), "job_completed", component="worker",
                      job_id=job.job_id, job_type=job.job_type)
        except asyncio.CancelledError:
            handler_task.cancel()
            await asyncio.gather(handler_task, return_exceptions=True)
            raise
        except PermanentJobError:
            await self.repository.fail(job.job_id, job.lease, datetime.now(timezone.utc), retryable=False)
            increment("netra_jobs_failed_total", job_type=job.job_type, status="permanent")
            log_event(logging.getLogger(__name__), "job_failed", component="worker",
                      job_id=job.job_id, job_type=job.job_type, error_type="PermanentJobError", level=logging.ERROR)
        except LeaseLostError:
            increment("netra_jobs_failed_total", job_type=job.job_type, status="lease_lost")
            return
        except Exception:
            delay = self.backoff.next_delay(job.attempt_count)
            await self.repository.fail(job.job_id, job.lease, datetime.now(timezone.utc) + delay)
            increment("netra_jobs_failed_total", job_type=job.job_type, status="retryable")
            increment("netra_jobs_retried_total", job_type=job.job_type, status="scheduled")
            log_event(logging.getLogger(__name__), "job_retry_scheduled", component="worker",
                      job_id=job.job_id, job_type=job.job_type, error_type="HandlerError")
        finally:
            observe("netra_job_duration_seconds", time.perf_counter() - started, job_type=job.job_type)
            if not renewal_task.done():
                renewal_task.cancel()
                await asyncio.gather(renewal_task, return_exceptions=True)

    async def _renew(self, job: Job) -> None:
        assert job.lease is not None
        interval = max(0.1, self.config.lease_duration_seconds / 3)
        while True:
            await asyncio.sleep(interval)
            expires = datetime.now(timezone.utc) + timedelta(seconds=self.config.lease_duration_seconds)
            try:
                job.lease = await self.repository.heartbeat(job.job_id, job.lease, expires)
            except Exception as exc:
                raise LeaseLostError("job lease renewal failed") from exc
