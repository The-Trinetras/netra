"""Migrations 0005 and 0009 must create exactly the tables M1 declared in M1_METADATA.

Runs the migration's ``upgrade()`` against a recording stand-in for
``alembic.op`` (no database) and compares every table, column type,
nullability, primary key, unique flag, foreign key and index with M1's
declarations, so either side changing without the other fails here.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

import netra_api.identity.postgres  # noqa: F401  (populates M1_METADATA)
import netra_api.session.postgres  # noqa: F401
from netra_api.platform.database import M1_METADATA

VERSIONS = Path(__file__).parents[2] / "migrations" / "versions"
MIGRATION = VERSIONS / "0005_m1_identity_session.py"
M1_MIGRATIONS = (MIGRATION, VERSIONS / "0009_m1_access_codes.py")
DIALECT = postgresql.dialect()


class _RecordingOp:
    def __init__(self) -> None:
        self.metadata = sa.MetaData()
        self.indexes: set[tuple[str, str, tuple[str, ...]]] = set()

    def create_table(self, name, *columns, **_kwargs):
        return sa.Table(name, self.metadata, *columns)

    def create_index(self, name, table, columns, unique=False, **_kwargs):
        self.indexes.add((table, name, tuple(columns)))


def _migrated() -> _RecordingOp:
    recorder = _RecordingOp()
    fake_alembic = types.ModuleType("alembic")
    fake_alembic.op = recorder
    saved = sys.modules.get("alembic")
    sys.modules["alembic"] = fake_alembic
    try:
        for path in M1_MIGRATIONS:
            spec = importlib.util.spec_from_file_location(path.stem, path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.upgrade()
    finally:
        if saved is not None:
            sys.modules["alembic"] = saved
        else:
            del sys.modules["alembic"]
    return recorder


def _shape(table: sa.Table) -> dict:
    return {
        column.name: (
            str(column.type.compile(dialect=DIALECT)),
            column.nullable,
            column.primary_key,
            bool(column.unique),
            sorted(fk.target_fullname for fk in column.foreign_keys),
        )
        for column in table.columns
    }


def test_migration_0005_matches_m1_metadata_exactly():
    migrated = _migrated()
    assert set(migrated.metadata.tables) == set(M1_METADATA.tables)
    for name, declared in M1_METADATA.tables.items():
        assert _shape(migrated.metadata.tables[name]) == _shape(declared), name


def test_migration_0005_creates_every_declared_index():
    migrated = _migrated()
    declared = {
        (table.name, index.name, tuple(column.name for column in index.columns))
        for table in M1_METADATA.tables.values()
        for index in table.indexes
    }
    assert migrated.indexes == declared


def test_migration_0005_follows_the_m2_head():
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'down_revision = "0004_d3_outbox_leases"' in source


def test_migration_0009_follows_0008_and_downgrades_what_it_creates():
    source = (VERSIONS / "0009_m1_access_codes.py").read_text(encoding="utf-8")
    assert 'down_revision = "0008_learning_questions_attempts"' in source
    assert 'op.drop_table("access_codes")' in source and 'op.drop_index("ix_access_codes_account_id"' in source
