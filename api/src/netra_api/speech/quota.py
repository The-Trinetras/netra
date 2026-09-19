"""Speech quota reservations.

message-flow.md flow 6: speech checks cache access and reserves quota before
fresh synthesis. data-ownership.md: reservations are not refunded merely
because playback stops — the provider may already have consumed quota.

D-QUOTA (20 September 2026): 20,000 characters per student per UTC day,
kept in PostgreSQL (speech/postgres.py). The unconfigured ledger refuses
every reservation (text delivery continues without speech).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from netra_api.platform.errors import ProviderUnavailableError, ResourceUnavailableError


class QuotaReservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reservation_id: UUID
    account_id: UUID
    characters: int = Field(ge=1)
    reserved_at: datetime


class SpeechQuotaExhaustedError(ResourceUnavailableError):
    """The student's speech allowance for today is used up; replies stay text only."""


class QuotaLedger(Protocol):
    async def reserve(self, account_id: UUID, characters: int) -> QuotaReservation:
        """Reserve before synthesis. Raises SpeechQuotaExhaustedError when exhausted."""
        ...


class UnconfiguredQuotaLedger:
    async def reserve(self, account_id: UUID, characters: int) -> QuotaReservation:
        raise ProviderUnavailableError("speech quota is not configured")


class InMemoryQuotaLedger:
    """Non-durable ledger for tests and labelled fixtures only."""

    def __init__(self, characters_per_account: int) -> None:
        self._limit = characters_per_account
        self._used: dict[UUID, int] = {}
        self._lock = asyncio.Lock()
        self.reservations: list[QuotaReservation] = []

    async def reserve(self, account_id: UUID, characters: int) -> QuotaReservation:
        async with self._lock:
            used = self._used.get(account_id, 0)
            if used + characters > self._limit:
                raise SpeechQuotaExhaustedError("speech quota exhausted")
            self._used[account_id] = used + characters
            reservation = QuotaReservation(
                reservation_id=uuid4(),
                account_id=account_id,
                characters=characters,
                reserved_at=datetime.now(timezone.utc),
            )
            self.reservations.append(reservation)
            return reservation
