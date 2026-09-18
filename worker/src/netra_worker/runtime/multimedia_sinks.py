"""PostgreSQL implementations of M3's worker ports (INT-04).

Adapters from M3's worker-side models (``netra_worker.jobs.multimedia``) to
M2's ``MultimediaCandidateStore``, plus the lease-bound ``JobStageRecorder``.
Construct them per claimed job in the worker composition root:

    store = MultimediaCandidateStore(session_factory)
    recorder = JobStageRecorder(session_factory, job)
    ExtractObjectJob(extraction, PostgresExtractionCandidateSink(store), recorder, kind=...)

Idempotency, citable handling and conflict rules live in the store; see
``netra_api.content.multimedia_store``.
"""

from __future__ import annotations

from typing import List, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from netra_api.content.multimedia_store import MultimediaCandidateStore
from netra_api.db.models import JobRow
from netra_worker.jobs.multimedia.extraction import ExtractionOutcome, is_citable
from netra_worker.jobs.multimedia.video import VideoEvidenceCandidateRecord
from netra_worker.runtime.errors import LeaseLostError
from netra_worker.runtime.job_repository import Job
from netra_worker.runtime.postgres import AsyncJobRepository


class PostgresExtractionCandidateSink:
    """``ExtractionCandidateSink`` for figure/chart/diagram/equation/table jobs."""

    def __init__(self, store: MultimediaCandidateStore) -> None:
        self._store = store

    async def store_candidate(self, *, source_version_id: UUID, outcome: ExtractionOutcome,
                              citable: bool, idempotency_key: str) -> None:
        await self._store.store_candidate(
            source_version_id=source_version_id,
            kind=outcome.kind.value,
            object_index=outcome.object_index,
            structure=outcome.structure,
            validation=outcome.validation.model_dump(mode="json"),
            findings=outcome.findings,
            citable=citable,
            # The store refuses citable=True unless this independent check passes.
            verified=is_citable(outcome),
            idempotency_key=idempotency_key,
        )


class PostgresVideoEvidenceSink:
    """``VideoEvidenceSink``: one stored batch per idempotency key, never citable."""

    def __init__(self, store: MultimediaCandidateStore) -> None:
        self._store = store

    async def store_candidates(self, *, video_id: UUID, candidates: List[VideoEvidenceCandidateRecord],
                               idempotency_key: str) -> None:
        await self._store.store_video_candidates(
            video_id=video_id,
            candidates=[
                {**candidate.model_dump(mode="python", exclude={"provenance"}),
                 "kind": getattr(candidate.kind, "value", candidate.kind),
                 "provenance": candidate.provenance.model_dump(mode="json")}
                for candidate in candidates
            ],
            idempotency_key=idempotency_key,
        )


class PostgresProviderBindingSink:
    """``ProviderBindingSink``: replaying the same binding is a no-op."""

    def __init__(self, store: MultimediaCandidateStore) -> None:
        self._store = store

    async def bind(self, *, video_id: UUID, provider: str, provider_index_id: str,
                   provider_video_id: str, model_name: str, model_version: str) -> None:
        await self._store.bind_provider(
            video_id=video_id, provider=provider, provider_index_id=provider_index_id,
            provider_video_id=provider_video_id, model_name=model_name, model_version=model_version)


class JobStageRecorder:
    """M3 ``StageRecorder`` bound to one claimed job and its live lease.

    ``record`` commits immediately through the fenced
    ``AsyncJobRepository.record_stage``: when the lease is no longer this
    worker's, it raises ``LeaseLostError`` and nothing is written, so a worker
    that lost ownership never records progress as the owner. The dispatcher
    renews ``job.lease`` in place, so the current lease is read at call time.
    """

    def __init__(self, sessions: async_sessionmaker[AsyncSession], job: Job) -> None:
        self._sessions = sessions
        self._job = job

    async def _row(self) -> JobRow:
        async with self._sessions() as session:
            row = (await session.execute(select(JobRow).where(JobRow.job_id == self._job.job_id))).scalar_one_or_none()
        if row is None:
            raise LeaseLostError("job no longer exists")
        return row

    async def completed_stages(self) -> List[str]:
        return list((await self._row()).completed_stages or [])

    async def remote_operation_id(self, stage: str) -> Optional[str]:
        return (await self._row()).remote_operation_ids.get(stage)

    async def record(self, stage: str, remote_operation_id: Optional[str] = None) -> None:
        if self._job.lease is None:
            raise LeaseLostError("job has no lease")
        async with self._sessions() as session:
            await AsyncJobRepository(session).record_stage(
                self._job.job_id, self._job.lease, stage, remote_operation_id)


__all__ = [
    "JobStageRecorder",
    "PostgresExtractionCandidateSink",
    "PostgresProviderBindingSink",
    "PostgresVideoEvidenceSink",
]
