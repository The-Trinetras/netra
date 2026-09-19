import os
from pathlib import Path

import pytest

from bootstrap_real_golden import PARSED, PDF
from netra_api.platform.database import create_engine, create_session_factory
from validate_real_golden import validate_against_database


# The database must first be restored by bootstrap_real_golden.py, which needs
# the local-only (non-redistributable) source documents and live Gemini/S3/
# Pinecone access. Without those documents the precondition cannot exist here.
@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.skipif(
    not (PDF.is_file() and PARSED.is_file()),
    reason="local-only evaluation/fixtures documents are absent; the golden source cannot be bootstrapped",
)
async def test_real_golden_dataset_matches_active_canonical_postgres_data():
    url = os.environ.get("NETRA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("NETRA_TEST_DATABASE_URL (a disposable local database) is not set")
    engine = create_engine(url)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            cases = await validate_against_database(
                Path(__file__).parents[1] / "cases" / "netra_e3_real_golden_v1.jsonl", session
            )
        assert len(cases) == 10
    finally:
        await engine.dispose()
