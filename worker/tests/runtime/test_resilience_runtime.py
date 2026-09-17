"""Deterministic worker lease-loss failure-path tests."""

import asyncio
from datetime import datetime, timezone
from uuid import uuid4
import pytest

from netra_worker.runtime.dispatcher import WorkerPool, WorkerPoolConfig
from netra_worker.runtime.job_repository import Job
from netra_worker.runtime.leases import Lease


def _job():
    now = datetime.now(timezone.utc)
    return Job(job_id=uuid4(), job_type="parse_document", payload={}, next_run_at=now,
               created_at=now, updated_at=now, lease=Lease(token=uuid4(), worker_id="worker", expires_at=now))


class LeaseLossRepo:
    def __init__(self): self.completed = []; self.failed = []
    async def complete(self, job_id, _lease): self.completed.append(job_id)
    async def fail(self, job_id, _lease, _next_run_at, retryable=True): self.failed.append((job_id, retryable))
    async def heartbeat(self, *_): raise RuntimeError("lease expired")


@pytest.mark.asyncio
async def test_lease_loss_cancels_handler_and_never_completes_job():
    repo = LeaseLossRepo(); started = asyncio.Event(); cancelled = asyncio.Event()
    async def handler(_job):
        started.set()
        try: await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set(); raise
    pool = WorkerPool(repo, {"parse_document": handler}, WorkerPoolConfig(
        worker_id="worker", job_types=("parse_document",), lease_duration_seconds=1))
    await asyncio.wait_for(asyncio.gather(pool._execute(_job()), started.wait()), 2)
    assert cancelled.is_set(); assert repo.completed == []; assert repo.failed == []
