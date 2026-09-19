"""The migrated disposable database matches every declared table and index.

Alembic owns the schema, but the ORM/Core metadata is what autogenerate
compares against. An object present only in a migration (for example a
partial unique index) would be proposed for removal by the next autogenerate,
silently dropping an invariant. Requires a database migrated to head.
"""

import os

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from pathlib import Path

import netra_api.identity.postgres  # noqa: F401  (populates M1_METADATA)
import netra_api.session.postgres  # noqa: F401
from netra_api.db.models import Base
from netra_api.platform.database import M1_METADATA, create_engine

pytestmark = pytest.mark.integration


async def test_migrated_schema_has_no_drift_from_declared_metadata():
    url = os.environ.get("NETRA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("NETRA_TEST_DATABASE_URL (a disposable local database) is not set")
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    head = ScriptDirectory.from_config(config).get_current_head()
    engine = create_engine(url)
    try:
        async with engine.connect() as connection:
            def inspect(sync):
                context = MigrationContext.configure(sync, opts={"compare_type": True})
                return context.get_current_revision(), compare_metadata(context, [Base.metadata, M1_METADATA])

            revision, differences = await connection.run_sync(inspect)
    finally:
        await engine.dispose()
    assert revision == head, "migrate the disposable database to head first"
    assert differences == []
