"""PostgreSQL outbox polling and allow-listed event-to-job dispatch.

Each claimed event becomes one durable job (idempotent by the producer's key)
and is acknowledged only after that job exists. A crash between the two
re-delivers the event, and the job's unique operation key absorbs the repeat.

Event handling is an explicit registry keyed by the API's allow-listed
``OUTBOX_EVENT_TYPES``; nothing is dispatched by name reflection.

- Invalid payload or an idempotency-key conflict: dead-lettered at once with a
  safe error code (retrying cannot fix it).
- An event type this worker has no handler for: left pending for a newer
  worker, then dead-lettered after ``max_unhandled_attempts`` claims.
- A lost claim: skipped; the other claimant owns the outcome.
- A database error while polling: backed off; never ends the consumer.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Mapping
from uuid import UUID

from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from netra_api.content.telemetry import increment, log_event, observe
from netra_api.db.outbox import (
    LEARNING_ATTEMPT_COMMITTED,
    OUTBOX_EVENT_TYPES,
    SOURCE_VERSION_INGESTION_REQUESTED,
)
from netra_api.platform.tracing import DISABLED_TRACER, Tracer
from netra_worker.jobs.ingestion.parse_document import ParseDocumentPayload
from netra_worker.jobs.learning_projection.neo4j import ProjectAssessmentAttemptPayload
from netra_worker.runtime.errors import LeaseLostError
from netra_worker.runtime.job_repository import JobPayload
from netra_worker.runtime.outbox import OutboxEvent
from netra_worker.runtime.postgres import AsyncJobRepository, AsyncOutboxRepository

logger = logging.getLogger(__name__)


class PoisonEventError(ValueError):
    """The event can never be dispatched; carries a safe error code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class JobRequest:
    job_type: str
    payload: JobPayload
    idempotency_key: str


EventHandler = Callable[[OutboxEvent], JobRequest]


class SourceVersionIngestionRequested(BaseModel):
    source_id: UUID
    source_version_id: UUID
    operation_key: str
    job_type: str


def ingestion_requested(event: OutboxEvent) -> JobRequest:
    request = SourceVersionIngestionRequested.model_validate(event.payload)
    expected = OUTBOX_EVENT_TYPES[SOURCE_VERSION_INGESTION_REQUESTED].job_type
    if request.job_type != expected or request.operation_key != f"{expected}:{request.source_version_id}":
        raise PoisonEventError("unsafe_ingestion_payload")
    fields = {key: value for key, value in event.payload.items() if key not in {"operation_key", "job_type"}}
    payload = ParseDocumentPayload(idempotency_key=request.operation_key, **fields)
    return JobRequest(expected, payload, request.operation_key)


def learning_attempt_committed(event: OutboxEvent) -> JobRequest:
    payload = ProjectAssessmentAttemptPayload.model_validate(event.payload)
    if payload.attempt_id != event.aggregate_id:
        raise PoisonEventError("aggregate_mismatch")
    return JobRequest(OUTBOX_EVENT_TYPES[LEARNING_ATTEMPT_COMMITTED].job_type, payload, payload.idempotency_key)


DEFAULT_HANDLERS: Mapping[str, EventHandler] = {
    SOURCE_VERSION_INGESTION_REQUESTED: ingestion_requested,
    LEARNING_ATTEMPT_COMMITTED: learning_attempt_committed,
}


class OutboxConsumer:
    """Drain outbox records and establish durable jobs before acknowledgement."""

    def __init__(self, factory: async_sessionmaker[AsyncSession], worker_id: str,
                 poll_interval_seconds: float = 1.0, lease_duration_seconds: int = 60,
                 batch_size: int = 10, tracer: Tracer | None = None,
                 error_backoff_seconds: float = 5.0,
                 handlers: Mapping[str, EventHandler] | None = None,
                 max_unhandled_attempts: int = 10,
                 job_repository: Callable[[AsyncSession], AsyncJobRepository] = AsyncJobRepository) -> None:
        if (poll_interval_seconds <= 0 or lease_duration_seconds < 1 or batch_size < 1
                or error_backoff_seconds <= 0 or max_unhandled_attempts < 1):
            raise ValueError("outbox consumer limits must be positive")
        unknown = set(handlers or DEFAULT_HANDLERS) - set(OUTBOX_EVENT_TYPES)
        if unknown:
            raise ValueError(f"handlers for non-allow-listed event types: {sorted(unknown)}")
        self.factory = factory
        self.worker_id = worker_id
        self.poll_interval_seconds = poll_interval_seconds
        self.lease_duration_seconds = lease_duration_seconds
        self.batch_size = batch_size
        self.tracer = tracer or DISABLED_TRACER
        self.error_backoff_seconds = error_backoff_seconds
        self.handlers = dict(DEFAULT_HANDLERS if handlers is None else handlers)
        self.max_unhandled_attempts = max_unhandled_attempts
        self._job_repository = job_repository
        self._stopping = asyncio.Event()

    async def poll_once(self) -> int:
        async with self.factory() as session:
            outbox = AsyncOutboxRepository(session)
            events = await outbox.claim_unprocessed(self.batch_size, self.worker_id,
                                                     self.lease_duration_seconds)
            for event in events:
                with self.tracer.span("worker.outbox_dispatch", **{"netra.operation": "outbox_dispatch",
                                                                   "netra.attempt": event.attempt_count}) as span:
                    outcome = await self._dispatch(session, outbox, event)
                    span.set(**{"netra.outcome": outcome})
            return len(events)

    async def _dispatch(self, session: AsyncSession, outbox: AsyncOutboxRepository, event: OutboxEvent) -> str:
        started = time.perf_counter()
        outcome = "error"
        try:
            outcome = await self._dispatch_one(session, outbox, event)
            return outcome
        except LeaseLostError:
            outcome = "claim_lost"
            return outcome
        finally:
            increment("netra_outbox_events_total", event_type=event.event_type, status=outcome)
            observe("netra_outbox_dispatch_duration_seconds", time.perf_counter() - started,
                    event_type=event.event_type)

    async def _dispatch_one(self, session: AsyncSession, outbox: AsyncOutboxRepository,
                            event: OutboxEvent) -> str:
        handler = self.handlers.get(event.event_type)
        if handler is None:
            if event.attempt_count >= self.max_unhandled_attempts:
                return await self._dead_letter(outbox, event, "unhandled_event_type")
            log_event(logger, "outbox_event_unhandled", component="outbox", event_id=event.event_id,
                      event_type=event.event_type, attempt=event.attempt_count, level=logging.WARNING)
            return "unhandled"
        try:
            request = handler(event)
        except PoisonEventError as exc:
            return await self._dead_letter(outbox, event, exc.code)
        except (ValidationError, TypeError, KeyError):
            return await self._dead_letter(outbox, event, "invalid_payload")
        try:
            await self._job_repository(session).enqueue(request.job_type, request.payload, request.idempotency_key)
        except ValueError:
            # The key already names a job of another type: retrying cannot help.
            return await self._dead_letter(outbox, event, "idempotency_conflict")
        await outbox.mark_processed(event.event_id, datetime.now(timezone.utc),
                                    event.claim_token, event.claim_worker_id)
        log_event(logger, "outbox_event_processed", component="outbox",
                  event_id=event.event_id, event_type=event.event_type, job_type=request.job_type)
        return "processed"

    async def _dead_letter(self, outbox: AsyncOutboxRepository, event: OutboxEvent, code: str) -> str:
        await outbox.mark_dead_lettered(event.event_id, event.claim_token, event.claim_worker_id, code)
        log_event(logger, "outbox_event_dead_lettered", component="outbox", event_id=event.event_id,
                  event_type=event.event_type, error_code=code, level=logging.ERROR)
        return "dead_lettered"

    async def run(self, stop_event: asyncio.Event | None = None) -> None:
        stop = stop_event or self._stopping
        while not stop.is_set():
            try:
                claimed = await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # A database outage must not end the consumer; unacknowledged
                # events are re-claimed after their claim lease expires.
                increment("netra_outbox_poll_errors_total")
                log_event(logger, "outbox_poll_failed", component="outbox",
                          error_type=type(exc).__name__, level=logging.WARNING)
                claimed, delay = 0, self.error_backoff_seconds
            else:
                delay = self.poll_interval_seconds
            if claimed:
                continue
            try:
                await asyncio.wait_for(stop.wait(), delay)
            except asyncio.TimeoutError:
                pass

    def stop(self) -> None:
        self._stopping.set()
