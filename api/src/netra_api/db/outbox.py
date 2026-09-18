"""API-callable transactional outbox port (INT-08).

Domain services in ``netra_api`` (for example M4's Learning service when it
commits an assessment attempt) write their outbox event with this port in the
*same* transaction as the canonical mutation. The port never commits, never
opens its own transaction and never imports worker runtime code; the worker's
outbox consumer later turns the event into a durable job.

Rules:
- Only allow-listed event types are accepted (``OUTBOX_EVENT_TYPES``), each
  owned by a domain. The payload is the producer's already-validated fact set;
  it must be JSON-serializable and carry no private text (producers own that).
- ``event_id`` is derived from the producer's idempotency key, and the insert
  is ``ON CONFLICT DO NOTHING``. A replayed commit therefore cannot create a
  second event, and at-least-once delivery collapses in the job's own key.
- No worker authority is fabricated: the event carries facts from the
  committed mutation; the worker never re-reads account-scoped state on the
  producer's behalf.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from netra_api.db.models import OutboxRow

_EVENT_NAMESPACE = uuid5(NAMESPACE_URL, "netra:outbox-event")


@dataclass(frozen=True)
class OutboxEventType:
    """One allow-listed event and the durable job the worker creates from it."""

    event_type: str
    aggregate_type: str
    job_type: str
    owner: str


SOURCE_VERSION_INGESTION_REQUESTED = "source_version.ingestion_requested"
LEARNING_ATTEMPT_COMMITTED = "learning.assessment_attempt_committed"

OUTBOX_EVENT_TYPES: Mapping[str, OutboxEventType] = {
    SOURCE_VERSION_INGESTION_REQUESTED: OutboxEventType(
        SOURCE_VERSION_INGESTION_REQUESTED, "source_version", "parse_document", "M2"),
    LEARNING_ATTEMPT_COMMITTED: OutboxEventType(
        LEARNING_ATTEMPT_COMMITTED, "assessment_attempt", "learning.project_assessment_attempt", "M4"),
}


def outbox_event_id(event_type: str, idempotency_key: str) -> UUID:
    """Deterministic event identity for one producer operation."""

    return uuid5(_EVENT_NAMESPACE, f"{event_type}:{idempotency_key}")


class TransactionalOutbox:
    """Write outbox events inside the caller's open transaction.

    Usage (inside the domain service's own transaction)::

        async with session.begin():
            ... canonical mutation ...
            await TransactionalOutbox().enqueue(
                session, event_type=LEARNING_ATTEMPT_COMMITTED,
                aggregate_id=attempt.attempt_id, payload=attempt_projection_payload(attempt))
    """

    async def enqueue(
        self,
        session: AsyncSession,
        *,
        event_type: str,
        aggregate_id: UUID,
        payload: Mapping[str, Any],
    ) -> UUID:
        spec = OUTBOX_EVENT_TYPES.get(event_type)
        if spec is None:
            raise ValueError(f"outbox event type is not allow-listed: {event_type!r}")
        if not session.in_transaction():
            # Writing outside the mutation's transaction would reintroduce the
            # dual-write gap the outbox exists to close.
            raise RuntimeError("outbox events must be written inside the caller's transaction")
        idempotency_key = payload.get("idempotency_key")
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            raise ValueError("outbox payload must carry the producer's idempotency_key")
        event_id = outbox_event_id(event_type, idempotency_key)
        await session.execute(
            insert(OutboxRow)
            .values(event_id=event_id, aggregate_type=spec.aggregate_type, aggregate_id=aggregate_id,
                    event_type=event_type, payload=dict(payload),
                    created_at=datetime.now(timezone.utc), attempt_count=0)
            .on_conflict_do_nothing(index_elements=[OutboxRow.event_id])
        )
        return event_id


__all__ = [
    "LEARNING_ATTEMPT_COMMITTED",
    "OUTBOX_EVENT_TYPES",
    "OutboxEventType",
    "SOURCE_VERSION_INGESTION_REQUESTED",
    "TransactionalOutbox",
    "outbox_event_id",
]
