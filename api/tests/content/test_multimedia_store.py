"""M3 candidate storage: fail-closed validation, schema alignment and DB idempotency."""

from __future__ import annotations

import importlib.util
import os
import sys
import types
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from netra_api.content.multimedia_store import (
    CandidateConflictError,
    MultimediaCandidateStore,
    PostgresTableCandidateSink,
)
from netra_api.db.models import Base
from netra_api.platform.auth_context import AuthContext
from netra_api.platform.errors import AuthorizationError

MIGRATION = Path(__file__).parents[2] / "migrations" / "versions" / "0006_m3_multimedia_candidates.py"
TABLES = ("multimedia_candidates", "video_evidence_candidates", "video_provider_bindings")


class _NoSessions:
    """Validation must fail before any database session is opened."""

    def __call__(self):
        raise AssertionError("no session may be opened for invalid input")


def _store() -> MultimediaCandidateStore:
    return MultimediaCandidateStore(_NoSessions())


def _candidate(**overrides):
    values = dict(source_version_id=uuid4(), kind="figure", structure={}, validation={},
                  citable=False, verified=False, idempotency_key="k-1")
    values.update(overrides)
    return values


@pytest.mark.parametrize("overrides", [
    {"citable": True, "verified": False},
    {"kind": "audio"},
    {"idempotency_key": " "},
    {"idempotency_key": "x" * 501},
    {"object_index": -1},
])
async def test_invalid_candidates_are_refused_before_storage(overrides):
    with pytest.raises(ValueError):
        await _store().store_candidate(**_candidate(**overrides))


async def test_video_candidates_must_belong_to_the_video_and_have_a_valid_range():
    video = uuid4()
    base = dict(video_id=video, source_version_id=uuid4(), locator="t=1", start_ms=10, end_ms=20,
                kind="visual", description="d", provenance={})
    with pytest.raises(ValueError, match="belong"):
        await _store().store_video_candidates(video_id=video, candidates=[{**base, "video_id": uuid4()}],
                                              idempotency_key="v")
    with pytest.raises(ValueError, match="range"):
        await _store().store_video_candidates(video_id=video, candidates=[{**base, "end_ms": 5}],
                                              idempotency_key="v")


async def test_provider_binding_fields_must_be_present():
    with pytest.raises(ValueError):
        await _store().bind_provider(video_id=uuid4(), provider="twelvelabs", provider_index_id="",
                                     provider_video_id="a", model_name="m", model_version="1")


async def test_table_sink_never_stores_a_table_as_citable():
    from netra_api.multimedia.validation import ValidationReport

    calls = []

    class Store:
        async def store_candidate(self, **kwargs):
            calls.append(kwargs)

    class Table:
        table_id = uuid4()

        def model_dump(self, mode):
            return {"table_id": str(self.table_id)}

    report = ValidationReport(object_id="t1")
    await PostgresTableCandidateSink(Store()).store_candidate(uuid4(), Table(), report, "table-1")
    [call] = calls
    assert call["kind"] == "table" and call["citable"] is False
    assert call["verified"] is report.is_source_verified
    assert call["validation"]["is_source_verified"] is report.is_source_verified


# --------------------------------------------------------------------------
# Migration 0006 <-> ORM alignment (no database)
# --------------------------------------------------------------------------


class _RecordingOp:
    def __init__(self) -> None:
        self.metadata = sa.MetaData()
        self.indexes: set[tuple[str, str, tuple[str, ...]]] = set()

    def create_table(self, name, *items, **_kwargs):
        return sa.Table(name, self.metadata, *items)

    def create_index(self, name, table, columns, **_kwargs):
        self.indexes.add((table, name, tuple(columns)))


def _migrated() -> _RecordingOp:
    recorder = _RecordingOp()
    fake = types.ModuleType("alembic")
    fake.op = recorder
    saved = sys.modules.get("alembic")
    sys.modules["alembic"] = fake
    try:
        spec = importlib.util.spec_from_file_location("m0006", MIGRATION)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        # The FK target must resolve inside the recording metadata.
        sa.Table("source_versions", recorder.metadata,
                 sa.Column("source_version_id", postgresql.UUID(as_uuid=True), primary_key=True))
        module.upgrade()
    finally:
        if saved is not None:
            sys.modules["alembic"] = saved
        else:
            del sys.modules["alembic"]
    return recorder


def _shape(table: sa.Table) -> dict:
    dialect = postgresql.dialect()
    columns = {
        column.name: (str(column.type.compile(dialect=dialect)), column.nullable, column.primary_key,
                      sorted(fk.target_fullname for fk in column.foreign_keys))
        for column in table.columns
    }
    constraints = sorted(
        constraint.name for constraint in table.constraints
        if isinstance(constraint, (sa.UniqueConstraint, sa.CheckConstraint)) and constraint.name
    )
    return {"columns": columns, "constraints": constraints}


def test_migration_0006_matches_the_orm_rows():
    migrated = _migrated()
    for name in TABLES:
        assert _shape(migrated.metadata.tables[name]) == _shape(Base.metadata.tables[name]), name
    declared = {(name, index.name, tuple(c.name for c in index.columns))
                for name in TABLES for index in Base.metadata.tables[name].indexes}
    assert migrated.indexes == declared


# --------------------------------------------------------------------------
# Database integration (disposable local database only)
# --------------------------------------------------------------------------


@pytest.fixture
async def sessions():
    url = os.environ.get("NETRA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("NETRA_TEST_DATABASE_URL (a disposable local database) is not set")
    from netra_api.platform.database import create_engine, create_session_factory

    engine = create_engine(url)
    try:
        yield create_session_factory(engine)
    finally:
        await engine.dispose()


async def _version(sessions, account_id):
    from netra_api.db.models import SourceRow, SourceVersionRow

    now = datetime.now(timezone.utc)
    source_id, version_id = uuid4(), uuid4()
    async with sessions() as session, session.begin():
        session.add(SourceRow(source_id=source_id, account_id=account_id, title="mm", created_at=now))
        await session.flush()
        session.add(SourceVersionRow(source_version_id=version_id, source_id=source_id, version_number=1,
                                     status="processing", is_active=False, created_at=now,
                                     parser_config={}, ingestion_state="parsing", completed_stages=[]))
    return source_id, version_id


async def _cleanup(sessions, source_id):
    from sqlalchemy import delete
    from netra_api.db.models import SourceRow

    async with sessions() as session, session.begin():
        await session.execute(delete(SourceRow).where(SourceRow.source_id == source_id))


def _auth(account_id):
    return AuthContext(account_id=account_id, session_id=uuid4(), request_id=uuid4(),
                       issued_at=datetime.now(timezone.utc))


@pytest.mark.integration
async def test_candidate_replay_stores_one_copy_and_key_reuse_conflicts(sessions):
    account = uuid4()
    source_id, version_id = await _version(sessions, account)
    store = MultimediaCandidateStore(sessions)
    try:
        key = f"extract:{uuid4()}"
        first = await store.store_candidate(source_version_id=version_id, kind="chart", object_index=2,
                                            structure={"a": 1}, validation={}, citable=False,
                                            verified=False, idempotency_key=key)
        # Retry re-ran extraction and produced different output: first copy wins.
        again = await store.store_candidate(source_version_id=version_id, kind="chart", object_index=2,
                                            structure={"a": 2}, validation={}, citable=False,
                                            verified=False, idempotency_key=key)
        assert again == first
        with pytest.raises(CandidateConflictError):
            await store.store_candidate(source_version_id=version_id, kind="chart", object_index=3,
                                        structure={}, validation={}, citable=False, verified=False,
                                        idempotency_key=key)
        stored = await store.list_candidates(_auth(account), version_id)
        assert [(item.object_index, item.structure) for item in stored] == [(2, {"a": 1})]
        with pytest.raises(AuthorizationError):
            await store.list_candidates(_auth(uuid4()), version_id)
    finally:
        await _cleanup(sessions, source_id)


@pytest.mark.integration
async def test_video_batch_and_binding_are_idempotent(sessions):
    account = uuid4()
    source_id, version_id = await _version(sessions, account)
    store = MultimediaCandidateStore(sessions)
    video = uuid4()
    batch = [dict(video_id=video, source_version_id=version_id, locator=f"t={i}", start_ms=i * 10,
                  end_ms=i * 10 + 5, kind="visual", description="d", provenance={"model": "m"})
             for i in range(3)]
    try:
        key = f"derive:{uuid4()}"
        assert await store.store_video_candidates(video_id=video, candidates=batch, idempotency_key=key) == 3
        assert await store.store_video_candidates(video_id=video, candidates=batch, idempotency_key=key) == 3
        await store.bind_provider(video_id=video, provider="twelvelabs", provider_index_id="idx",
                                  provider_video_id="asset-1", model_name="marengo", model_version="3")
        await store.bind_provider(video_id=video, provider="twelvelabs", provider_index_id="idx",
                                  provider_video_id="asset-1", model_name="marengo", model_version="3")
        with pytest.raises(CandidateConflictError):
            await store.bind_provider(video_id=video, provider="twelvelabs", provider_index_id="idx",
                                      provider_video_id="asset-2", model_name="marengo", model_version="3")
    finally:
        from sqlalchemy import delete
        from netra_api.db.models import VideoProviderBindingRow

        async with sessions() as session, session.begin():
            await session.execute(delete(VideoProviderBindingRow).where(VideoProviderBindingRow.video_id == video))
        await _cleanup(sessions, source_id)
