"""D-QUOTA ledger on real PostgreSQL. Needs the disposable database at head."""

import asyncio
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from netra_api.platform.database import create_engine
from netra_api.speech.postgres import PostgresQuotaLedger
from netra_api.speech.quota import SpeechQuotaExhaustedError

pytestmark = pytest.mark.integration


@pytest.fixture
async def engine():
    url = os.environ.get("NETRA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("NETRA_TEST_DATABASE_URL (a disposable local database) is not set")
    engine = create_engine(url)
    try:
        yield engine
    finally:
        await engine.dispose()


async def test_daily_limit_holds_under_concurrency_and_resets_next_day(engine):
    today = datetime(2026, 9, 21, 23, 59, tzinfo=timezone.utc)
    clock = {"now": today}
    ledger = PostgresQuotaLedger(engine, 1_000, clock=lambda: clock["now"])
    account = uuid4()

    results = await asyncio.gather(*(ledger.reserve(account, 300) for _ in range(5)), return_exceptions=True)
    assert sum(not isinstance(r, Exception) for r in results) == 3
    assert all(isinstance(r, SpeechQuotaExhaustedError) for r in results if isinstance(r, Exception))
    await ledger.reserve(account, 100)  # exactly the limit
    with pytest.raises(SpeechQuotaExhaustedError):
        await ledger.reserve(account, 1)

    clock["now"] = today + timedelta(minutes=2)  # the next UTC day
    await ledger.reserve(account, 1_000)
    with pytest.raises(SpeechQuotaExhaustedError):
        await ledger.reserve(uuid4(), 1_001)
