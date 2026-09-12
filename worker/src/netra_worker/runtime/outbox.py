"""Outbox abstraction for projections that follow a committed PostgreSQL write.

CLAUDE.md "Background jobs": "Use an outbox when a committed PostgreSQL
mutation requires a later projection/update." `enqueue` is written in
the same transaction as the business mutation it follows; a worker job
later drains the outbox and performs the (idempotent) projection — e.g.
Neo4j or Pinecone — without ever holding that original transaction open
across the external call.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Protocol
from uuid import UUID

from pydantic import BaseModel


class OutboxEvent(BaseModel):
    """One durable record of a projection/update owed after a committed mutation."""

    event_id: UUID
    aggregate_type: str
    aggregate_id: UUID
    event_type: str
    payload: Dict[str, Any]
    created_at: datetime
    processed_at: Optional[datetime] = None
    attempt_count: int = 0


class OutboxRepository(Protocol):
    """Typed contract for writing and draining outbox events.

    `enqueue` must be called within the same PostgreSQL transaction as
    the business mutation it follows from; `claim_unprocessed` is read
    by a worker job on its own, separate transaction.
    """

    def enqueue(
        self, aggregate_type: str, aggregate_id: UUID, event_type: str, payload: Dict[str, Any]
    ) -> OutboxEvent:
        ...

    def claim_unprocessed(self, limit: int) -> List[OutboxEvent]:
        ...

    def mark_processed(self, event_id: UUID, processed_at: datetime) -> None:
        ...
