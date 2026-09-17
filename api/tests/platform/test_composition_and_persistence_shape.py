"""Production composition fails closed; PostgreSQL statements compile for the proposed schema.

The SQL checks compile statements with the PostgreSQL dialect only. No database
was available, so they do NOT verify migrations, transactions or asyncpg
behaviour — that requires M2's reviewed migration and a disposable local database.
"""

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import and_, update
from sqlalchemy.dialects import postgresql

from netra_api.bootstrap import UnavailableRepository, build_production
from netra_api.config import Settings
from netra_api.identity.service import UnconfiguredCredentialVerifier
from netra_api.platform.database import M1_METADATA, create_engine
from netra_api.platform.errors import AuthenticationRequiredError, ResourceUnavailableError
from netra_api.session import postgres as session_tables
from netra_api.identity import postgres as identity_tables
from netra_api.speech.recognition import TranscriptEvent, accept_final_transcript, turn_from_final_transcript
from netra_api.transport.http.auth import extract_bearer
from netra_api.transport.http.health import liveness


async def test_production_without_database_or_auth_registers_nothing_that_can_succeed():
    composition = build_production(Settings(database_url=None, auth_mode="stored_credential", trace_to_log=False))
    assert isinstance(composition.verifier, UnconfiguredCredentialVerifier)
    assert isinstance(composition.services.sessions._repository, UnavailableRepository)
    assert composition.services.coordinator is None and composition.services.speech is None
    assert composition.registered["persistence"] is False
    assert not any(composition.registered.values())
    with pytest.raises(AuthenticationRequiredError):
        await composition.verifier.verify("anything")
    with pytest.raises(ResourceUnavailableError):
        await composition.services.sessions._repository.get(uuid4())


def test_in_memory_repositories_are_never_imported_by_composition():
    import netra_api.bootstrap as bootstrap

    source = open(bootstrap.__file__, encoding="utf-8").read()
    for module in ("netra_api.identity.memory", "netra_api.session.memory", "InMemoryQuotaLedger", "InMemoryAudioCache"):
        assert module not in source


def test_only_the_approved_async_driver_is_accepted():
    with pytest.raises(ResourceUnavailableError):
        create_engine("sqlite:///netra.db")


def test_liveness_reports_booleans_only():
    assert liveness({"persistence": 0, "tool:search_sources": 1}) == {"status": "ok", "registered": {"persistence": False, "tool:search_sources": True}}


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({"authorization": "Bearer abc123"}, "abc123"),
        ({"Authorization": "bearer abc123"}, "abc123"),
        ({"authorization": "Basic abc123"}, None),
        ({"authorization": "Bearer a b"}, None),
        ({}, None),
    ],
)
def test_bearer_extraction(headers, expected):
    assert extract_bearer(headers) == expected


def test_only_final_transcripts_become_turns():
    assert accept_final_transcript(TranscriptEvent(text="next", is_final=False)) is None
    assert turn_from_final_transcript(TranscriptEvent(text="next", is_final=False), 3) is None
    assert accept_final_transcript(TranscriptEvent(text="   ", is_final=True)) is None
    turn = turn_from_final_transcript(TranscriptEvent(text=" where am I ", is_final=True), 3)
    assert turn.utterance == "where am I" and turn.transcript_status == "final"


def _sql(statement):
    return str(statement.compile(dialect=postgresql.dialect()))


def test_proposed_m1_tables_are_declared():
    assert set(M1_METADATA.tables) >= {
        "accounts",
        "account_devices",
        "account_credentials",
        "session_bindings",
        "sessions",
        "session_request_records",
        "session_result_sets",
        "session_dialogue_entries",
    }
    assert [c.name for c in session_tables.session_request_records.primary_key] == ["account_id", "request_id"]
    assert identity_tables.account_credentials.c.token_sha256.unique


def test_versioned_update_is_conditional_on_expected_version():
    sessions = session_tables.sessions
    statement = update(sessions).where(and_(sessions.c.session_id == uuid4(), sessions.c.session_version == 5)).values(session_version=6)
    sql = _sql(statement)
    assert "WHERE sessions.session_id = %(session_id_1)s::UUID AND sessions.session_version = %(session_version_1)s" in sql


def test_dialogue_append_is_idempotent_on_request_and_ordinal():
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    table = session_tables.session_dialogue_entries
    statement = pg_insert(table).values(request_id=uuid4(), ordinal=0, session_id=uuid4(), role="student", content="x", created_at=datetime.now(timezone.utc)).on_conflict_do_nothing(index_elements=["request_id", "ordinal"])
    assert "ON CONFLICT (request_id, ordinal) DO NOTHING" in _sql(statement)
