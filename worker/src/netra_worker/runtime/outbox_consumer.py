"""PostgreSQL outbox polling and allow-listed job dispatch."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from netra_api.content.sources.ingestion import SOURCE_VERSION_INGESTION_REQUESTED
from netra_worker.jobs.ingestion.parse_document import ParseDocumentPayload
from netra_worker.runtime.postgres import AsyncJobRepository, AsyncOutboxRepository
from netra_api.platform.observability import increment, log_event, observe, start_span

logger = logging.getLogger(__name__)


class SourceVersionIngestionRequested(BaseModel):
    source_id: UUID
    source_version_id: UUID
    operation_key: str
    job_type: str


class OutboxConsumer:
    """Drain outbox records and establish durable jobs before acknowledgement."""

    def __init__(self, factory: async_sessionmaker[AsyncSession], worker_id: str,
                 poll_interval_seconds: float = 1.0, lease_duration_seconds: int = 60,
                 batch_size: int = 10) -> None:
        if poll_interval_seconds <= 0 or lease_duration_seconds < 1 or batch_size < 1:
            raise ValueError("outbox consumer limits must be positive")
        self.factory = factory
        self.worker_id = worker_id
        self.poll_interval_seconds = poll_interval_seconds
        self.lease_duration_seconds = lease_duration_seconds
        self.batch_size = batch_size
        self._stopping = asyncio.Event()

    async def poll_once(self) -> int:
        async with self.factory() as session:
            outbox = AsyncOutboxRepository(session)
            events = await outbox.claim_unprocessed(self.batch_size, self.worker_id,
                                                     self.lease_duration_seconds)
            for event in events:
                await self._dispatch(session, outbox, event)
            return len(events)

    async def _dispatch(self, session: AsyncSession, outbox: AsyncOutboxRepository, event) -> None:
        started = time.perf_counter()
        increment("netra_outbox_events_claimed_total", event_type=event.event_type, status="claimed")
        if event.event_type != SOURCE_VERSION_INGESTION_REQUESTED:
            logger.error("unknown outbox event left pending", extra={"event_id": str(event.event_id),
                         "event_type": event.event_type})
            increment("netra_outbox_events_failed_total", event_type=event.event_type, status="unknown")
            observe("netra_outbox_dispatch_duration_seconds", time.perf_counter() - started, event_type=event.event_type)
            return
        try:
            request = SourceVersionIngestionRequested.model_validate(event.payload)
        except Exception:
            logger.error("invalid ingestion outbox event left pending", extra={"event_id": str(event.event_id)})
            increment("netra_outbox_events_failed_total", event_type=event.event_type, status="invalid")
            observe("netra_outbox_dispatch_duration_seconds", time.perf_counter() - started, event_type=event.event_type)
            return
        if request.job_type != "parse_document" or request.operation_key != f"parse_document:{request.source_version_id}":
            logger.error("unsafe ingestion outbox payload left pending", extra={"event_id": str(event.event_id)})
            increment("netra_outbox_events_failed_total", event_type=event.event_type, status="unsafe")
            observe("netra_outbox_dispatch_duration_seconds", time.perf_counter() - started, event_type=event.event_type)
            return
        job_repo = AsyncJobRepository(session)
        # The source creation transaction already inserts this job. This
        # idempotent enqueue also repairs a legacy event whose job was absent.
        payload_data = dict(event.payload)
        payload_data.pop("operation_key", None)
        payload_data.pop("job_type", None)
        payload = ParseDocumentPayload(
            idempotency_key=request.operation_key,
            **payload_data,
        )
        await job_repo.enqueue(request.job_type, payload, request.operation_key)
        await outbox.mark_processed(event.event_id, datetime.now(timezone.utc),
                                    event.claim_token, event.claim_worker_id)
        increment("netra_outbox_events_processed_total", event_type=event.event_type, status="processed")
        log_event(logging.getLogger(__name__), "outbox_event_processed", component="outbox",
                  event_id=event.event_id, event_type=event.event_type)
        observe("netra_outbox_dispatch_duration_seconds", time.perf_counter() - started, event_type=event.event_type)

    async def run(self, stop_event: asyncio.Event | None = None) -> None:
        stop = stop_event or self._stopping
        while not stop.is_set():
            claimed = await self.poll_once()
            if claimed:
                continue
            try:
                await asyncio.wait_for(stop.wait(), self.poll_interval_seconds)
            except asyncio.TimeoutError:
                pass

    def stop(self) -> None:
        self._stopping.set()
