"""Contract fixtures M3 supplies to M2 for INT-04 (sinks and stage recording).

Each ``check_*`` coroutine states one requirement the multimedia jobs rely
on, as an executable assertion. M2 runs them against the real PostgreSQL
implementations on a disposable database; M3 runs them here against the
TEST-ONLY reference doubles in test_recording_and_contracts.py. Passing
against the doubles proves the checks are coherent, not that any real
store satisfies them.

Exact signatures under test (from netra_worker.jobs.multimedia.*):

- JobRepository.record_stage(job_id: UUID, lease: Lease, stage: str,
  remote_operation_id: str | None = None) -> Job   (sync or async)
- ExtractionCandidateSink.store_candidate(*, source_version_id: UUID,
  outcome: ExtractionOutcome, citable: bool, idempotency_key: str) -> None
- VideoEvidenceSink.store_candidates(*, video_id: UUID,
  candidates: list[VideoEvidenceCandidateRecord], idempotency_key: str) -> None
- ProviderBindingSink.bind(*, video_id: UUID, provider: str,
  provider_index_id: str, provider_video_id: str, model_name: str,
  model_version: str) -> None

Probes are read-back helpers the implementer supplies for the test only;
they are not part of the production interface.
"""

from __future__ import annotations

import inspect
from typing import Any, Awaitable, Callable, List, Protocol
from uuid import UUID, uuid4

from netra_worker.jobs.multimedia.extraction import ExtractionOutcome
from netra_worker.jobs.multimedia.video import VideoEvidenceCandidateRecord
from netra_worker.runtime.job_repository import Job
from netra_worker.runtime.leases import Lease


async def _call(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


# ---------------------------------------------------------------- stage recording


async def check_record_stage_returns_the_updated_job(repository: Any, job: Job) -> None:
    updated = await _call(repository.record_stage(job.job_id, job.lease, "index_video", "asset-1"))
    assert isinstance(updated, Job)
    assert "index_video" in updated.completed_stages
    assert updated.remote_operation_ids.get("index_video") == "asset-1"


async def check_record_stage_rejects_a_lost_lease(repository: Any, job: Job) -> None:
    stale = Lease(token=uuid4(), worker_id="someone-else", expires_at=job.lease.expires_at)
    try:
        await _call(repository.record_stage(job.job_id, stale, "bind_provider_asset", None))
    except Exception:
        pass
    else:
        raise AssertionError("record_stage accepted a write from a worker that does not hold the lease")
    after = await _call(repository.record_stage(job.job_id, job.lease, "probe_stage", None))
    assert "bind_provider_asset" not in after.completed_stages


async def check_record_stage_is_idempotent_per_stage(repository: Any, job: Job) -> None:
    await _call(repository.record_stage(job.job_id, job.lease, "derive_video_evidence", None))
    again = await _call(repository.record_stage(job.job_id, job.lease, "derive_video_evidence", None))
    assert again.completed_stages.count("derive_video_evidence") == 1


# ---------------------------------------------------------------- sinks


class ExtractionProbe(Protocol):
    async def stored(self, idempotency_key: str) -> List[bool]:
        """One entry per stored copy for this key: its citable flag."""
        ...


class VideoProbe(Protocol):
    async def stored(self, idempotency_key: str) -> List[List[VideoEvidenceCandidateRecord]]:
        ...


class BindingProbe(Protocol):
    async def bindings(self, video_id: UUID) -> List[dict]:
        ...


async def check_extraction_sink_is_idempotent_and_keeps_citable_false(
    sink: Any, probe: ExtractionProbe, outcome: ExtractionOutcome, source_version_id: UUID
) -> None:
    key = f"contract-{uuid4()}"
    for _ in range(2):
        await sink.store_candidate(source_version_id=source_version_id, outcome=outcome, citable=False, idempotency_key=key)
    assert await probe.stored(key) == [False]


async def check_video_sink_is_idempotent(
    sink: Any, probe: VideoProbe, video_id: UUID, candidates: List[VideoEvidenceCandidateRecord]
) -> None:
    key = f"contract-{uuid4()}"
    for _ in range(2):
        await sink.store_candidates(video_id=video_id, candidates=candidates, idempotency_key=key)
    stored = await probe.stored(key)
    assert len(stored) == 1 and len(stored[0]) == len(candidates)


async def check_binding_sink_is_idempotent_and_keeps_pins(sink: Any, probe: BindingProbe, video_id: UUID) -> None:
    binding = dict(
        video_id=video_id, provider="twelvelabs", provider_index_id="idx", provider_video_id="ia-1",
        model_name="pinned-model", model_version="pinned-version",
    )
    await sink.bind(**binding)
    await sink.bind(**binding)
    stored = await probe.bindings(video_id)
    assert len(stored) == 1
    assert {k: stored[0][k] for k in ("provider_video_id", "model_name", "model_version")} == {
        "provider_video_id": "ia-1", "model_name": "pinned-model", "model_version": "pinned-version"
    }


ALL_RECORDER_CHECKS: List[Callable[[Any, Job], Awaitable[None]]] = [
    check_record_stage_returns_the_updated_job,
    check_record_stage_rejects_a_lost_lease,
    check_record_stage_is_idempotent_per_stage,
]
