"""Neo4j writer against the installed neo4j==6.3.0 driver API (no server, no network).

The driver double is ``create_autospec(AsyncDriver)``, so every call is checked
against the real ``execute_query``/``close`` signatures. Responses are real
``EagerResult`` values. This proves request/response shape and failure
mapping only; it is not evidence that the Cypher runs on Neo4j 5.26.
"""

from datetime import datetime, timezone
from unittest.mock import create_autospec
from uuid import uuid4

import pytest
from neo4j import AsyncDriver, EagerResult, Query, RoutingControl
from neo4j.exceptions import AuthError, CypherSyntaxError, ServiceUnavailable, TransientError

from netra_worker.jobs.learning_projection.neo4j import (
    ATTEMPTED_EDGE_CYPHER,
    AttemptedEdge,
    ConceptNotProjectedError,
    ProjectAssessmentAttemptJob,
    ProjectAssessmentAttemptPayload,
    ProjectionWrite,
)
from netra_worker.jobs.learning_projection.neo4j_writer import LearningProjectionSettings, Neo4jFactualGraphWriter
from netra_worker.runtime.errors import PermanentJobError


def _edge():
    return AttemptedEdge(account_id=uuid4(), concept_id="ohms-law", attempt_id=uuid4(), outcome="correct",
                         hints_used=1, evaluated_by="grader", occurred_at=datetime(2026, 9, 18, tzinfo=timezone.utc))


def _driver(records):
    driver = create_autospec(AsyncDriver, instance=True)
    driver.execute_query.return_value = EagerResult(records=records, summary=None, keys=["attempt_id"])
    return driver


async def test_the_reviewed_cypher_runs_with_bound_parameters_write_routing_and_timeouts():
    driver = _driver([{"attempt_id": "x"}])
    edge = _edge()
    writer = Neo4jFactualGraphWriter(driver, database="netra", query_timeout_seconds=5)
    assert await writer.upsert_attempted(edge) is ProjectionWrite.PROJECTED
    (query,), kwargs = driver.execute_query.call_args
    assert isinstance(query, Query) and query.text == ATTEMPTED_EDGE_CYPHER and query.timeout == 5
    assert kwargs["routing_"] is RoutingControl.WRITE and kwargs["database_"] == "netra"
    assert kwargs["parameters_"] == {
        "account_id": str(edge.account_id), "concept_id": "ohms-law", "attempt_id": str(edge.attempt_id),
        "outcome": "correct", "hints_used": 1, "evaluated_by": "grader", "occurred_at": "2026-09-18T00:00:00+00:00"}


async def test_no_rows_means_the_concept_is_not_projected_and_the_job_retries():
    writer = Neo4jFactualGraphWriter(_driver([]), database="neo4j", query_timeout_seconds=5)
    assert await writer.upsert_attempted(_edge()) is ProjectionWrite.CONCEPT_NOT_PROJECTED
    payload = ProjectAssessmentAttemptPayload(
        idempotency_key="k", account_id=uuid4(), attempt_id=uuid4(), concept_id="ohms-law", outcome="partial",
        hints_used=0, evaluated_by="tutor", occurred_at=datetime.now(timezone.utc))
    with pytest.raises(ConceptNotProjectedError):
        await ProjectAssessmentAttemptJob(writer).handle(payload)


@pytest.mark.parametrize("error", [AuthError("secret-bearing message"), CypherSyntaxError("bad")])
async def test_auth_and_cypher_errors_are_permanent_without_echoing_messages(error):
    driver = _driver([])
    driver.execute_query.side_effect = error
    with pytest.raises(PermanentJobError) as caught:
        await Neo4jFactualGraphWriter(driver, database="neo4j", query_timeout_seconds=5).upsert_attempted(_edge())
    assert "secret" not in str(caught.value) and caught.value.__cause__ is None


@pytest.mark.parametrize("error", [ServiceUnavailable("down"), TransientError("busy")])
async def test_availability_errors_stay_retryable(error):
    driver = _driver([])
    driver.execute_query.side_effect = error
    with pytest.raises(type(error)):
        await Neo4jFactualGraphWriter(driver, database="neo4j", query_timeout_seconds=5).upsert_attempted(_edge())


def test_settings_disable_the_projection_until_fully_configured(monkeypatch):
    for key in ("URI", "USER", "PASSWORD"):
        monkeypatch.delenv(f"NETRA_NEO4J_{key}", raising=False)
    assert not LearningProjectionSettings().configured
    monkeypatch.setenv("NETRA_NEO4J_URI", "neo4j://127.0.0.1:7687")
    monkeypatch.setenv("NETRA_NEO4J_USER", "netra")
    monkeypatch.setenv("NETRA_NEO4J_PASSWORD", "not-logged")
    settings = LearningProjectionSettings()
    assert settings.configured and "not-logged" not in repr(settings)
