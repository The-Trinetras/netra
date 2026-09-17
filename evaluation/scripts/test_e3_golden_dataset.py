from pathlib import Path

import pytest

from netra_api.config import Settings
from netra_api.platform.database import create_engine, create_session_factory
from validate_real_golden import validate_against_database


@pytest.mark.asyncio
@pytest.mark.integration
async def test_real_golden_dataset_matches_active_canonical_postgres_data():
    engine = create_engine(Settings())
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            cases = await validate_against_database(
                Path(__file__).parents[1] / "cases" / "netra_e3_real_golden_v1.jsonl", session
            )
        assert len(cases) == 10
    finally:
        await engine.dispose()
