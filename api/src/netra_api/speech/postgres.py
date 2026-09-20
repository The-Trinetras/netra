"""PostgreSQL speech quota ledger (decision D-QUOTA, 20 September 2026).

20,000 characters of synthesized speech per student per UTC day (the amount
is Settings.speech_daily_characters). One atomic upsert per reservation:
the day's counter only moves while it stays within the limit, so concurrent
segments can never overshoot it. Reservations are not refunded when playback
stops (data-ownership.md): the provider may already have spent them.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Callable
from uuid import UUID, uuid4

from sqlalchemy import Column, Date, Integer, Table
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.dialects.postgresql import insert as pg_insert

from netra_api.platform.database import M1_METADATA
from netra_api.speech.quota import QuotaReservation, SpeechQuotaExhaustedError

speech_quota_usage = Table(
    "speech_quota_usage",
    M1_METADATA,
    Column("account_id", PGUUID(as_uuid=True), primary_key=True),
    Column("usage_day", Date, primary_key=True),
    Column("characters_used", Integer, nullable=False),
)
"""D-QUOTA daily speech characters per account (migration 0011)."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PostgresQuotaLedger:
    def __init__(self, engine: Any, characters_per_day: int, clock: Callable[[], datetime] = _utcnow) -> None:
        if characters_per_day < 1:
            raise ValueError("characters_per_day must be positive")
        self._engine = engine
        self._limit = characters_per_day
        self._clock = clock

    async def reserve(self, account_id: UUID, characters: int) -> QuotaReservation:
        now = self._clock()
        if characters > self._limit:
            raise SpeechQuotaExhaustedError("segment exceeds the daily speech limit")
        day: date = now.astimezone(timezone.utc).date()
        table = speech_quota_usage
        statement = (
            pg_insert(table)
            .values(account_id=account_id, usage_day=day, characters_used=characters)
            .on_conflict_do_update(
                index_elements=["account_id", "usage_day"],
                set_={"characters_used": table.c.characters_used + characters},
                where=table.c.characters_used + characters <= self._limit,
            )
            .returning(table.c.characters_used)
        )
        async with self._engine.begin() as connection:
            if (await connection.execute(statement)).first() is None:
                raise SpeechQuotaExhaustedError("speech quota exhausted for today")
        return QuotaReservation(reservation_id=uuid4(), account_id=account_id, characters=characters, reserved_at=now)
