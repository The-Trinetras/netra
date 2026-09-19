"""Lease-aware stage recording and the INT-04 contract fixtures.

The repository and sinks below are TEST-ONLY in-memory reference doubles
standing in for M2's unpublished PostgreSQL implementations. They exist to
prove the contract checks are coherent and to exercise the M3 bridge; they
are not persistence and must never be registered in composition.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import contracts  # noqa: E402

from netra_worker.jobs.multimedia.base import JobCancelledError, run_stages  # noqa: E402
from netra_worker.jobs.multimedia.extraction import ExtractedObjectKind, ExtractionOutcome, ValidationVerdict  # noqa: E402
from netra_worker.jobs.multimedia.recording import (  # noqa: E402
    LeaseCancellationToken,
    LeaseHolder,
    RepositoryStageRecorder,
)
from netra_worker.jobs.multimedia.video import (  # noqa: E402
    EvidenceProvenanceRecord,
    VideoEvidenceCandidateRecord,
    VideoEvidenceKindName,
)
from netra_worker.runtime.errors import JobCancelled, LeaseLostError  # noqa: E402
from netra_worker.runtime.job_repository import Job  # noqa: E402
from netra_worker.runtime.leases import Lease  # noqa: E402

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)



class ReferenceJobRepository:
    """TEST-ONLY: record_stage semantics only."""

    def __init__(self, job: Job) -> None:
        self.job = job
        self.writes = 0

    def record_stage(self, job_id, lease, stage, remote_operation_id=None):
        if job_id != self.job.job_id or self.job.lease is None or lease.token != self.job.lease.token:
            raise LeaseLostError("lease is not current")
        self.writes += 1
        stages = list(self.job.completed_stages)
        if stage not in stages:
            stages.append(stage)
        ids = dict(self.job.remote_operation_ids)
        if remote_operation_id is not None:
            ids[stage] = remote_operation_id
        self.job = self.job.model_copy(update={"completed_stages": stages, "remote_operation_ids": ids})
        return self.job

    def reclaim(self, worker_id: str) -> Lease:
        """Simulate the lease expiring and another worker claiming the job."""

        lease = Lease(token=uuid4(), worker_id=worker_id, expires_at=NOW + timedelta(minutes=5))
        self.job = self.job.model_copy(update={"lease": lease})
        return lease


def _job(stages=(), ids=None) -> Job:
    lease = Lease(token=uuid4(), worker_id="w1", expires_at=NOW + timedelta(minutes=5))
    return Job(
        job_id=uuid4(), job_type="multimedia.index_video", payload={}, next_run_at=NOW, lease=lease,
        created_at=NOW, updated_at=NOW, completed_stages=list(stages), remote_operation_ids=dict(ids or {}),
    )


# ---------------------------------------------------------------- recorder bridge


async def test_recorder_resumes_from_the_jobs_recorded_stages_and_ids():
    job = _job(stages=["index_video"], ids={"index_video": "ia-1"})
    recorder = RepositoryStageRecorder(ReferenceJobRepository(job), job, LeaseHolder(job.lease))
    assert await recorder.completed_stages() == ["index_video"]
    assert await recorder.remote_operation_id("index_video") == "ia-1"


async def test_run_stages_through_the_repository_skips_completed_work_on_retry():
    job = _job()
    repository = ReferenceJobRepository(job)
    calls = []

    async def index():
        calls.append("index")
        return "ia-1"

    async def bind():
        calls.append("bind")
        raise RuntimeError("worker crashed before binding")

    with pytest.raises(RuntimeError):
        await run_stages(RepositoryStageRecorder(repository, job, LeaseHolder(job.lease)), [("index_video", index), ("bind_provider_asset", bind)])

    async def bind_ok():
        calls.append("bind")
        return None

    retry_job = repository.job
    executed = await run_stages(
        RepositoryStageRecorder(repository, retry_job, LeaseHolder(retry_job.lease)),
        [("index_video", index), ("bind_provider_asset", bind_ok)],
    )
    assert executed == ["bind_provider_asset"]
    assert calls == ["index", "bind", "bind"]
    assert repository.job.remote_operation_ids == {"index_video": "ia-1"}


async def test_a_lost_lease_rejects_the_write_and_the_error_propagates():
    job = _job()
    repository = ReferenceJobRepository(job)
    recorder = RepositoryStageRecorder(repository, job, LeaseHolder(job.lease))
    repository.reclaim("w2")
    with pytest.raises(LeaseLostError):
        await recorder.record("index_video", "ia-1")
    assert await recorder.completed_stages() == []


async def test_recorder_refuses_a_repository_that_did_not_keep_the_id():
    job = _job()

    class Forgetful(ReferenceJobRepository):
        def record_stage(self, job_id, lease, stage, remote_operation_id=None):
            return super().record_stage(job_id, lease, stage, None)

    with pytest.raises(RuntimeError):
        await RepositoryStageRecorder(Forgetful(job), job, LeaseHolder(job.lease)).record("index_video", "ia-1")


def test_recorder_must_be_built_for_the_current_lease():
    job = _job()
    with pytest.raises(ValueError):
        RepositoryStageRecorder(ReferenceJobRepository(job), job, LeaseHolder(Lease(token=uuid4(), worker_id="w", expires_at=NOW)))


async def test_async_repositories_are_awaited():
    job = _job()
    inner = ReferenceJobRepository(job)

    class AsyncRepository:
        async def record_stage(self, *args):
            return inner.record_stage(*args)

    recorder = RepositoryStageRecorder(AsyncRepository(), job, LeaseHolder(job.lease))
    await recorder.record("derive_video_evidence")
    assert await recorder.completed_stages() == ["derive_video_evidence"]


# ---------------------------------------------------------------- lease cancellation


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


class Explicit:
    def __init__(self, cancelled):
        self.cancelled = cancelled

    def is_cancelled(self):
        return self.cancelled

    def reason(self):
        return "student pressed STOP"


def test_lease_token_cancels_within_the_safety_margin_and_renewal_extends_it():
    job = _job()
    holder = LeaseHolder(job.lease)
    clock = Clock(NOW)
    token = LeaseCancellationToken(holder, safety_margin=timedelta(seconds=30), clock=clock)
    assert not token.is_cancelled()
    clock.now = job.lease.expires_at - timedelta(seconds=10)
    assert token.is_cancelled()
    holder.renew(job.lease.model_copy(update={"expires_at": job.lease.expires_at + timedelta(minutes=5)}))
    assert not token.is_cancelled()
    with pytest.raises(ValueError):
        holder.renew(Lease(token=uuid4(), worker_id="w1", expires_at=NOW))


def test_explicit_cancellation_wins_and_reports_its_reason():
    job = _job()
    token = LeaseCancellationToken(LeaseHolder(job.lease), safety_margin=timedelta(0), explicit=Explicit(True), clock=Clock(NOW))
    assert token.is_cancelled() and token.reason() == "student pressed STOP"


async def test_an_expired_lease_stops_the_job_before_its_next_provider_stage():
    job = _job()
    repository = ReferenceJobRepository(job)
    holder = LeaseHolder(job.lease)
    clock = Clock(NOW)
    token = LeaseCancellationToken(holder, safety_margin=timedelta(seconds=30), clock=clock)
    called = []

    async def first():
        called.append("first")
        clock.now = job.lease.expires_at  # the lease lapses while the first stage runs
        return None

    async def second():
        called.append("second")
        return None

    # Lease loss, not cancellation: the dispatcher writes nothing and the next
    # claimant resumes from the recorded stage "a".
    with pytest.raises(LeaseLostError):
        await run_stages(RepositoryStageRecorder(repository, job, holder), [("a", first), ("b", second)], cancellation=token)
    assert called == ["first"] and repository.job.completed_stages == ["a"]


async def test_explicit_cancellation_is_a_cancellation_not_a_lease_loss():
    job = _job()
    token = LeaseCancellationToken(LeaseHolder(job.lease), safety_margin=timedelta(0), explicit=Explicit(True), clock=Clock(NOW))
    assert not token.lease_expired()
    with pytest.raises(JobCancelledError) as caught:
        await run_stages(RepositoryStageRecorder(ReferenceJobRepository(job), job, LeaseHolder(job.lease)),
                         [("a", lambda: None)], cancellation=token)
    assert isinstance(caught.value, JobCancelled)


def test_a_tracking_holder_sees_the_dispatchers_renewal_of_the_job():
    job = _job()
    holder = LeaseHolder.tracking(job)
    clock = Clock(job.lease.expires_at - timedelta(seconds=10))
    token = LeaseCancellationToken(holder, safety_margin=timedelta(seconds=30), clock=clock)
    assert token.is_cancelled() and token.lease_expired()
    # The dispatcher's heartbeat replaces job.lease (same token, later expiry).
    job.lease = job.lease.model_copy(update={"expires_at": job.lease.expires_at + timedelta(minutes=5)})
    assert not token.is_cancelled()
    # A different token would be a new claim; the holder must not adopt it.
    job.lease = Lease(token=uuid4(), worker_id="w2", expires_at=NOW + timedelta(hours=1))
    assert holder.lease.token != job.lease.token


# ---------------------------------------------------------------- contract fixtures vs reference doubles


@pytest.mark.parametrize("check", contracts.ALL_RECORDER_CHECKS, ids=lambda check: check.__name__)
async def test_reference_repository_satisfies_the_recorder_contract(check):
    job = _job()
    await check(ReferenceJobRepository(job), job)


class ReferenceExtractionSink:
    """TEST-ONLY."""

    def __init__(self):
        self.rows = {}

    async def store_candidate(self, *, source_version_id, outcome, citable, idempotency_key):
        self.rows.setdefault(idempotency_key, citable)

    async def stored(self, idempotency_key):
        return [self.rows[idempotency_key]] if idempotency_key in self.rows else []


class ReferenceVideoSink:
    """TEST-ONLY."""

    def __init__(self):
        self.rows = {}

    async def store_candidates(self, *, video_id, candidates, idempotency_key):
        self.rows.setdefault(idempotency_key, list(candidates))

    async def stored(self, idempotency_key):
        return [self.rows[idempotency_key]] if idempotency_key in self.rows else []


class ReferenceBindingSink:
    """TEST-ONLY."""

    def __init__(self):
        self.rows = {}

    async def bind(self, **binding):
        key = (binding["video_id"], binding["provider"], binding["provider_index_id"], binding["provider_video_id"])
        self.rows.setdefault(key, binding)

    async def bindings(self, video_id):
        return [row for (vid, *_), row in self.rows.items() if vid == video_id]


async def test_reference_sinks_satisfy_the_sink_contracts():
    outcome = ExtractionOutcome(kind=ExtractedObjectKind.TABLE, object_index=1, structure={}, validation=ValidationVerdict())
    sink = ReferenceExtractionSink()
    await contracts.check_extraction_sink_is_idempotent_and_keeps_citable_false(sink, sink, outcome, uuid4())

    video_id = uuid4()
    candidate = VideoEvidenceCandidateRecord(
        video_id=video_id, source_version_id=uuid4(), locator="lecture-v1", start_ms=0, end_ms=30_000,
        kind=VideoEvidenceKindName.VISUAL_DESCRIPTION, description="d",
        provenance=EvidenceProvenanceRecord(provider="twelvelabs", model_name="m", model_version="v", produced_at=NOW, stage="derive_video_evidence"),
    )
    video_sink = ReferenceVideoSink()
    await contracts.check_video_sink_is_idempotent(video_sink, video_sink, video_id, [candidate])

    binding_sink = ReferenceBindingSink()
    await contracts.check_binding_sink_is_idempotent_and_keeps_pins(binding_sink, binding_sink, video_id)


async def test_the_contract_catches_a_duplicating_sink():
    class Duplicating(ReferenceExtractionSink):
        async def store_candidate(self, *, source_version_id, outcome, citable, idempotency_key):
            self.rows.setdefault(idempotency_key, [])
            self.rows[idempotency_key].append(citable)

        async def stored(self, idempotency_key):
            return self.rows.get(idempotency_key, [])

    outcome = ExtractionOutcome(kind=ExtractedObjectKind.TABLE, object_index=1, structure={}, validation=ValidationVerdict())
    sink = Duplicating()
    with pytest.raises(AssertionError):
        await contracts.check_extraction_sink_is_idempotent_and_keeps_citable_false(sink, sink, outcome, UUID(int=1))
