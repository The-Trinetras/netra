import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from netra_worker.runtime.dispatcher import PermanentJobError, WorkerPool, WorkerPoolConfig
from netra_worker.runtime.job_repository import Job, JobStatus
from netra_worker.runtime.leases import Lease


def _job(job_type="parse_document"):
    now = datetime.now(timezone.utc)
    return Job(job_id=uuid4(), job_type=job_type, payload={}, next_run_at=now,
               created_at=now, updated_at=now,
               lease=Lease(token=uuid4(), worker_id="pool-0", expires_at=now))


class Repo:
    def __init__(self, jobs):
        self.jobs, self.claimed, self.completed, self.failed, self.heartbeats = list(jobs), [], [], [], []

    async def claim_next(self, job_types, worker_id, lease_duration_seconds):
        for job in self.jobs:
            if job.job_type in job_types and job not in self.claimed:
                self.claimed.append(job)
                job.lease = job.lease.model_copy(update={"worker_id": worker_id})
                return job
        return None

    async def complete(self, job_id, lease): self.completed.append(job_id)
    async def fail(self, job_id, lease, next_run_at, retryable=True): self.failed.append((job_id, retryable))
    async def heartbeat(self, job_id, lease, new_expires_at):
        self.heartbeats.append(job_id)
        return lease.model_copy(update={"expires_at": new_expires_at})


@pytest.mark.asyncio
async def test_pool_filters_job_types_and_stops_without_busy_loop():
    repo = Repo([_job("parse_document"), _job("embed_text")])
    handled = []
    stop = asyncio.Event()

    async def handle(job):
        handled.append(job.job_type)
        stop.set()

    pool = WorkerPool(repo, {"parse_document": handle}, WorkerPoolConfig(
        worker_id="pool", job_types=("parse_document",), concurrency=2, poll_interval_seconds=0.01))
    await asyncio.wait_for(pool.run(stop), 1)
    assert handled == ["parse_document"]
    assert all(job.job_type == "parse_document" for job in repo.claimed)


@pytest.mark.asyncio
async def test_pool_marks_retryable_and_permanent_failures():
    retry = _job()
    permanent = _job()
    repo = Repo([retry, permanent])
    calls = 0

    async def handle(_job):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary")
        raise PermanentJobError("invalid")

    pool = WorkerPool(repo, {"parse_document": handle}, WorkerPoolConfig(
        worker_id="pool", job_types=("parse_document",), poll_interval_seconds=0.01))
    stop = asyncio.Event()
    async def stop_later():
        while len(repo.failed) < 2:
            await asyncio.sleep(0.01)
        stop.set()
    await asyncio.gather(pool.run(stop), stop_later())
    assert repo.failed == [(retry.job_id, True), (permanent.job_id, False)]


from pydantic import BaseModel

from netra_worker.runtime.dispatcher import LeaseLostError


class _FlakyRepo(Repo):
    """claim_next fails once (database blip) before returning jobs."""

    def __init__(self, jobs):
        super().__init__(jobs)
        self.claim_errors = 0

    async def claim_next(self, job_types, worker_id, lease_duration_seconds):
        if self.claim_errors == 0:
            self.claim_errors += 1
            raise ConnectionError("database unavailable")
        return await super().claim_next(job_types, worker_id, lease_duration_seconds)


@pytest.mark.asyncio
async def test_claim_errors_back_off_instead_of_killing_the_worker():
    repo = _FlakyRepo([_job()])
    stop = asyncio.Event()

    async def handle(_job):
        stop.set()

    pool = WorkerPool(repo, {"parse_document": handle}, WorkerPoolConfig(
        worker_id="pool", job_types=("parse_document",), poll_interval_seconds=0.01,
        repository_error_backoff_seconds=0.01))
    await asyncio.wait_for(pool.run(stop), 1)
    assert repo.claim_errors == 1
    assert len(repo.completed) == 1


@pytest.mark.asyncio
async def test_invalid_payload_is_dead_lettered_not_retried():
    class Payload(BaseModel):
        required: int

    repo = Repo([_job()])

    async def handle(job):
        Payload.model_validate(job.payload)

    pool = WorkerPool(repo, {"parse_document": handle}, WorkerPoolConfig(
        worker_id="pool", job_types=("parse_document",)))
    await pool._execute(repo.jobs[0])
    assert repo.failed == [(repo.jobs[0].job_id, False)]


class _FencedRepo(Repo):
    async def complete(self, job_id, lease):
        raise LeaseLostError("job lease is no longer valid")

    async def fail(self, job_id, lease, next_run_at, retryable=True):
        raise AssertionError("a fenced completion must not be converted into a failure")


@pytest.mark.asyncio
async def test_fenced_completion_is_lease_loss_not_a_retry_or_crash():
    repo = _FencedRepo([_job()])

    async def handle(_job):
        return None

    pool = WorkerPool(repo, {"parse_document": handle}, WorkerPoolConfig(
        worker_id="pool", job_types=("parse_document",)))
    await pool._execute(repo.jobs[0])  # must not raise
    assert repo.completed == []


class _BrokenFailRepo(Repo):
    async def fail(self, job_id, lease, next_run_at, retryable=True):
        raise ConnectionError("database unavailable")


@pytest.mark.asyncio
async def test_unrecorded_failure_does_not_escape_the_attempt():
    repo = _BrokenFailRepo([_job()])

    async def handle(_job):
        raise RuntimeError("provider timeout")

    pool = WorkerPool(repo, {"parse_document": handle}, WorkerPoolConfig(
        worker_id="pool", job_types=("parse_document",)))
    await pool._execute(repo.jobs[0])  # outcome is retried after lease expiry


@pytest.mark.asyncio
async def test_each_attempt_is_one_span_with_sanitized_facts():
    from netra_api.platform.tracing import InMemorySpanExporter, build_tracer

    exporter = InMemorySpanExporter()
    tracer = build_tracer("local", exporter=exporter)
    repo = Repo([_job()])

    async def handle(_job):
        raise RuntimeError("secret provider detail")

    pool = WorkerPool(repo, {"parse_document": handle}, WorkerPoolConfig(
        worker_id="pool", job_types=("parse_document",)), tracer=tracer)
    await pool._execute(repo.jobs[0])
    tracer.shutdown(2.0)
    [span] = exporter.spans
    assert span.name == "worker.job_attempt"
    assert span.attributes["netra.operation"] == "parse_document"
    assert span.attributes["netra.outcome"] == "retry_scheduled"
    assert "secret provider detail" not in str(span.attributes)


class _CancellingRepo(Repo):
    def __init__(self, jobs):
        super().__init__(jobs)
        self.cancelled = []

    async def cancel(self, job_id, lease):
        self.cancelled.append(job_id)


@pytest.mark.asyncio
async def test_a_cancelled_job_is_recorded_cancelled_not_retried_or_dead_lettered():
    from netra_worker.runtime.errors import JobCancelled

    job = _job()
    repo = _CancellingRepo([job])
    stop = asyncio.Event()

    async def handle(_job):
        stop.set()
        raise JobCancelled("student pressed STOP")

    pool = WorkerPool(repo, {"parse_document": handle}, WorkerPoolConfig(
        worker_id="pool", job_types=("parse_document",), poll_interval_seconds=0.01))
    await asyncio.wait_for(pool.run(stop), 1)
    assert repo.cancelled == [job.job_id] and repo.failed == [] and repo.completed == []
