"""D-BUDGET ledger on real PostgreSQL. Needs the disposable database at head."""

import asyncio
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from netra_api.coordinator.budget_ledger import BudgetUsage
from netra_api.platform.database import create_engine
from netra_api.platform.errors import IdempotencyConflictError
from netra_api.session.postgres import PostgresBudgetLedger

pytestmark = pytest.mark.integration


@pytest.fixture
async def ledger():
    url = os.environ.get("NETRA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("NETRA_TEST_DATABASE_URL (a disposable local database) is not set")
    engine = create_engine(url)
    try:
        yield PostgresBudgetLedger(engine)
    finally:
        await engine.dispose()


async def test_open_is_once_and_counters_only_grow(ledger):
    account, request, session = uuid4(), uuid4(), uuid4()
    start = datetime.now(timezone.utc).replace(microsecond=0)
    assert await ledger.open(account, request, session, start) == BudgetUsage(start)
    await asyncio.gather(*(ledger.record(account, request, BudgetUsage(start, d, t, 0)) for d, t in ((2, 1), (1, 5), (3, 2))))
    again = await ledger.open(account, request, session, start + timedelta(minutes=5))
    assert again == BudgetUsage(start, 3, 5, 0)
    with pytest.raises(IdempotencyConflictError):
        await ledger.open(account, request, uuid4(), start)
