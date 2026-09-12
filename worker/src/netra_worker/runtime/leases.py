"""Lease/claim value objects for worker job execution.

A lease represents exclusive, time-bounded ownership of one job by one
worker process. Keeping this a plain value object — rather than baking
expiry logic into the repository — lets the PostgreSQL-backed
JobRepository implementation change its claim query (e.g. to adopt
`SELECT ... FOR UPDATE SKIP LOCKED`) without changing what a lease means
to callers.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class Lease(BaseModel):
    """Exclusive, time-bounded claim on one job."""

    token: UUID
    worker_id: str
    expires_at: datetime

    def is_expired(self, now: datetime) -> bool:
        return now >= self.expires_at
