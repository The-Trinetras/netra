"""M2 retrieval fixtures: freshness, integrity and (with a test DB) resolver outcomes."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from m2_retrieval_fixtures import OUTPUT, build

REASONS = {"resolved", "not_found", "unauthorized", "deleted", "source_version_mismatch"}


def _fixture() -> dict:
    return json.loads(OUTPUT.read_text(encoding="utf-8"))


def test_committed_fixture_matches_the_generator():
    assert _fixture() == json.loads(json.dumps(build(), sort_keys=True))


def test_fixture_integrity_and_negative_coverage():
    fixture = _fixture()
    versions = {item["source_version_id"]: item for item in fixture["versions"]}
    chunks = {item["chunk_id"]: item for item in fixture["chunks"]}
    assert len(chunks) == len(fixture["chunks"]), "chunk IDs must be unique"
    assert all(item["source_version_id"] in versions for item in chunks.values())
    assert sum(1 for item in versions.values() if item["is_active"] and item["source_id"] == versions[
        next(iter(versions))]["source_id"]) == 1, "one active version per source"
    seen = set()
    for query in fixture["queries"]:
        assert set(query["relevance"]) <= set(chunks)
        assert all(grade in (1, 2) for grade in query["relevance"].values())
        assert set(query["expected_resolution"].values()) <= REASONS
        seen |= set(query["expected_resolution"].values())
    assert {"resolved", "not_found", "unauthorized", "source_version_mismatch"} <= seen
    assert fixture["incompatible_vectors"], "an incompatible-embedding case is required"
    assert any(query["relevance"] == {} for query in fixture["queries"]), "a no-evidence case is required"


@pytest.fixture
async def seeded():
    url = os.environ.get("NETRA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("NETRA_TEST_DATABASE_URL (a disposable local database) is not set")
    from sqlalchemy import delete
    from netra_api.db.models import ReadingBlockRow, SearchChunkRow, SourceRow, SourceVersionRow
    from netra_api.platform.database import create_engine, create_session_factory

    fixture = _fixture()
    engine = create_engine(url)
    factory = create_session_factory(engine)
    now = datetime.now(timezone.utc)
    live = [item for item in fixture["versions"] if not item.get("deleted")]
    sources = {item["source_id"]: item["account_id"] for item in live}
    async with factory() as session, session.begin():
        for source_id, account_id in sources.items():
            session.add(SourceRow(source_id=UUID(source_id), account_id=UUID(account_id), title="fixture",
                                  created_at=now))
        await session.flush()
        for item in live:
            session.add(SourceVersionRow(
                source_version_id=UUID(item["source_version_id"]), source_id=UUID(item["source_id"]),
                version_number=item["version_number"], status=item["status"], is_active=item["is_active"],
                created_at=now, parser_config={}, ingestion_state="active" if item["is_active"] else "ready",
                completed_stages=[]))
        await session.flush()
        live_ids = {item["source_version_id"] for item in live}
        for ordinal, chunk in enumerate(fixture["chunks"]):
            if chunk["source_version_id"] not in live_ids:
                continue
            block_id = uuid4()
            session.add(ReadingBlockRow(block_id=block_id, source_version_id=UUID(chunk["source_version_id"]),
                                        sequence_id=ordinal, kind="paragraph", text=chunk["text"],
                                        structured_location={"locator": chunk["locator"]}, sentences=[]))
            session.add(SearchChunkRow(chunk_id=UUID(chunk["chunk_id"]),
                                       source_version_id=UUID(chunk["source_version_id"]), text=chunk["text"],
                                       block_ids=[str(block_id)], embedding_version=chunk["embedding_spec"],
                                       retrieval_metadata={}))
    try:
        async with factory() as session:
            yield fixture, session
    finally:
        async with factory() as session, session.begin():
            for source_id in sources:
                await session.execute(delete(SourceRow).where(SourceRow.source_id == UUID(source_id)))
        await engine.dispose()


@pytest.mark.integration
async def test_resolver_returns_the_expected_outcome_for_every_probe(seeded):
    from netra_api.content.retrieval.postgres_evidence import AsyncPostgresEvidenceResolver
    from netra_api.platform.auth_context import AuthContext

    fixture, session = seeded
    auth = AuthContext(account_id=UUID(fixture["account_id"]), session_id=uuid4(), request_id=uuid4(),
                       issued_at=datetime.now(timezone.utc))
    resolver = AsyncPostgresEvidenceResolver(session)
    for query in fixture["queries"]:
        probes = list(query["expected_resolution"])
        if not probes:
            continue
        scope = query["scope_source_version_ids"]
        results = await resolver.resolve(
            auth, probes, allowed_source_version_ids=[UUID(v) for v in scope] if scope else None,
            require_active=scope is None)
        outcomes = {r.evidence_id: "resolved" if r.is_resolved else r.rejection_reason.value for r in results}
        assert outcomes == query["expected_resolution"], query["query_id"]
