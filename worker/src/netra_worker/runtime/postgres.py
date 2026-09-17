"""Async PostgreSQL implementations of the worker job and outbox contracts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from netra_api.db.models import JobRow, OutboxRow
from netra_worker.runtime.job_repository import Job, JobPayload, JobStatus
from netra_worker.runtime.leases import Lease
from netra_worker.runtime.outbox import OutboxEvent


def _job(row: JobRow) -> Job:
    lease = Lease(token=row.lease_token, worker_id=row.worker_id, expires_at=row.lease_until) if row.lease_token and row.worker_id and row.lease_until else None
    return Job(job_id=row.job_id, job_type=row.job_type, payload=row.payload, status=row.status,
               attempt_count=row.attempts, max_attempts=row.max_attempts, next_run_at=row.next_run_at,
               lease=lease, created_at=row.created_at, updated_at=row.updated_at,
               completed_stages=row.completed_stages, remote_operation_ids=row.remote_operation_ids)


class AsyncJobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def enqueue(self, job_type: str, payload: JobPayload, idempotency_key: str, max_attempts: int = 5) -> Job:
        existing = (await self.session.execute(select(JobRow).where(JobRow.operation_key == idempotency_key))).scalar_one_or_none()
        if existing:
            # The lookup autobegins a read transaction. Close it before a
            # caller starts another explicit transaction, such as the
            # outbox acknowledgement immediately after idempotent enqueue.
            await self.session.commit()
            return _job(existing)
        now = datetime.now(timezone.utc)
        row = JobRow(job_id=uuid4(), job_type=job_type, status=JobStatus.PENDING.value, attempts=0,
                     max_attempts=max_attempts, next_run_at=now, operation_key=idempotency_key,
                     payload=payload.model_dump(mode="json"), completed_stages=[], remote_operation_ids={},
                     created_at=now, updated_at=now)
        self.session.add(row)
        try:
            await self.session.commit()
        except IntegrityError:
            # Another worker/request won the unique operation_key race.
            # Rollback is required here to clear this failed transaction;
            # unlike the old pre-transaction rollbacks, it cannot discard
            # caller work because this repository owns the failed insert.
            await self.session.rollback()
            existing = (await self.session.execute(select(JobRow).where(
                JobRow.operation_key == idempotency_key))).scalar_one()
            await self.session.commit()
            return _job(existing)
        return _job(row)

    async def claim_next(self, job_types: list[str], worker_id: str, lease_duration_seconds: int) -> Job | None:
        now = datetime.now(timezone.utc)
        async with self.session.begin():
            row = (await self.session.execute(select(JobRow).where(
                JobRow.job_type.in_(job_types), JobRow.status.in_([JobStatus.PENDING.value, JobStatus.LEASED.value]),
                JobRow.next_run_at <= now, (JobRow.lease_until.is_(None) | (JobRow.lease_until < now))
            ).order_by(JobRow.next_run_at, JobRow.created_at).limit(1).with_for_update(skip_locked=True))).scalar_one_or_none()
            if not row: return None
            row.status, row.attempts, row.worker_id, row.lease_token = JobStatus.LEASED.value, row.attempts + 1, worker_id, uuid4()
            row.lease_until = now + timedelta(seconds=lease_duration_seconds); row.updated_at = now
        return _job(row)

    async def _owned(self, job_id: UUID, lease: Lease, values: dict) -> JobRow:
        row = (await self.session.execute(select(JobRow).where(JobRow.job_id == job_id,
            JobRow.status == JobStatus.LEASED.value, JobRow.lease_token == lease.token,
            JobRow.worker_id == lease.worker_id, JobRow.lease_until >= datetime.now(timezone.utc)).with_for_update())).scalar_one_or_none()
        if row is None: raise RuntimeError("job lease is no longer valid")
        for key, value in values.items(): setattr(row, key, value)
        row.updated_at = datetime.now(timezone.utc)
        return row

    async def heartbeat(self, job_id: UUID, lease: Lease, new_expires_at: datetime) -> Lease:
        async with self.session.begin(): await self._owned(job_id, lease, {"lease_until": new_expires_at})
        return Lease(token=lease.token, worker_id=lease.worker_id, expires_at=new_expires_at)

    async def complete(self, job_id: UUID, lease: Lease) -> None:
        async with self.session.begin(): await self._owned(job_id, lease, {"status": JobStatus.COMPLETED.value, "lease_until": None})

    async def fail(self, job_id: UUID, lease: Lease, next_run_at: datetime, retryable: bool = True) -> None:
        async with self.session.begin():
            row = await self._owned(job_id, lease, {"next_run_at": next_run_at, "lease_until": None, "last_error": "job failed"})
            row.status = (JobStatus.PENDING.value if retryable and row.attempts < row.max_attempts
                          else JobStatus.DEAD_LETTER.value)

    async def record_stage(self, job_id: UUID, lease: Lease, stage: str, remote_operation_id: str | None = None) -> Job:
        async with self.session.begin():
            row = await self._owned(job_id, lease, {})
            stages = list(row.completed_stages or [])
            if stage not in stages: stages.append(stage)
            row.completed_stages = stages
            if remote_operation_id:
                ids = dict(row.remote_operation_ids or {}); ids[stage] = remote_operation_id; row.remote_operation_ids = ids
        return _job(row)


class AsyncOutboxRepository:
    def __init__(self, session: AsyncSession) -> None: self.session = session

    async def enqueue(self, aggregate_type: str, aggregate_id: UUID, event_type: str, payload: dict) -> OutboxEvent:
        row = OutboxRow(event_id=uuid4(), aggregate_type=aggregate_type, aggregate_id=aggregate_id,
                        event_type=event_type, payload=payload, created_at=datetime.now(timezone.utc), attempt_count=0)
        self.session.add(row)
        return OutboxEvent.model_validate({"event_id": row.event_id, "aggregate_type": aggregate_type,
            "aggregate_id": aggregate_id, "event_type": event_type, "payload": payload,
            "created_at": row.created_at, "attempt_count": 0})

    @staticmethod
    def _event(row: OutboxRow) -> OutboxEvent:
        return OutboxEvent.model_validate({"event_id": row.event_id, "aggregate_type": row.aggregate_type,
            "aggregate_id": row.aggregate_id, "event_type": row.event_type, "payload": row.payload,
            "created_at": row.created_at, "processed_at": row.processed_at, "attempt_count": row.attempt_count,
            "claim_token": row.claim_token, "claim_worker_id": row.claim_worker_id,
            "claim_until": row.claim_until})

    async def claim_unprocessed(self, limit: int, worker_id: str, lease_duration_seconds: int) -> list[OutboxEvent]:
        if limit < 1 or lease_duration_seconds < 1:
            raise ValueError("limit and lease duration must be positive")
        now = datetime.now(timezone.utc)
        claim_until = now + timedelta(seconds=lease_duration_seconds)
        async with self.session.begin():
            rows = (await self.session.execute(select(OutboxRow).where(OutboxRow.processed_at.is_(None))
                .where(OutboxRow.claim_until.is_(None) | (OutboxRow.claim_until < now))
                .order_by(OutboxRow.created_at).limit(limit).with_for_update(skip_locked=True))).scalars().all()
            for row in rows:
                row.attempt_count += 1
                row.claim_token, row.claim_worker_id, row.claim_until = uuid4(), worker_id, claim_until
        return [self._event(row) for row in rows]

    async def mark_processed(self, event_id: UUID, processed_at: datetime, claim_token: UUID, worker_id: str) -> None:
        async with self.session.begin():
            result = await self.session.execute(update(OutboxRow).where(
                OutboxRow.event_id == event_id, OutboxRow.processed_at.is_(None),
                OutboxRow.claim_token == claim_token, OutboxRow.claim_worker_id == worker_id,
                OutboxRow.claim_until >= datetime.now(timezone.utc),
            ).values(processed_at=processed_at, claim_token=None, claim_worker_id=None, claim_until=None))
            if result.rowcount != 1:
                raise RuntimeError("outbox claim is no longer valid")
