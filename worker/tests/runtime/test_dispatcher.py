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
