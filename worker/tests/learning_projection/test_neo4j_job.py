"""Factual learning-graph projection job (D4).

In-memory writer double only: these prove the job's semantics, not Neo4j
behaviour. ATTEMPTED_EDGE_CYPHER has not been executed against a database
(none is available or authorized here). The producer/consumer payload
contract is tested in tests/contract/test_learning_projection_payload.py.
"""

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from netra_worker.jobs.learning_projection.neo4j import (
    ATTEMPTED_EDGE_CYPHER,
    AttemptedEdge,
    ConceptNotProjectedError,
    ProjectAssessmentAttemptJob,
    ProjectAssessmentAttemptPayload,
    ProjectionWrite,
)


class _InMemoryGraph:
    """Labelled double reproducing the Cypher's documented semantics:
    MATCH the concept, create-only relationship keyed by attempt_id."""

    def __init__(self, concepts=("concept-ohms-law",)):
        self.concepts = set(concepts)
        self.edges: dict[UUID, AttemptedEdge] = {}
        self.writes = 0

    async def upsert_attempted(self, edge):
        if edge.concept_id not in self.concepts:
            return ProjectionWrite.CONCEPT_NOT_PROJECTED
        if edge.attempt_id not in self.edges:
            self.edges[edge.attempt_id] = edge
            self.writes += 1
        return ProjectionWrite.PROJECTED


def _payload(**overrides):
    attempt_id = overrides.pop("attempt_id", uuid4())
    defaults = dict(
        idempotency_key=f"learning.project_assessment_attempt:{attempt_id}",
        account_id=uuid4(),
        attempt_id=attempt_id,
        concept_id="concept-ohms-law",
        outcome="incorrect",
        hints_used=2,
        evaluated_by="tutor",
        occurred_at=datetime(2026, 9, 18, 10, 0, tzinfo=timezone.utc),
    )
    defaults.update(overrides)
    return ProjectAssessmentAttemptPayload(**defaults)


async def test_projection_writes_one_factual_edge_per_attempt():
    graph = _InMemoryGraph()
    payload = _payload()
    await ProjectAssessmentAttemptJob(graph).handle(payload)

    edge = graph.edges[payload.attempt_id]
    assert (edge.outcome, edge.hints_used, edge.evaluated_by) == ("incorrect", 2, "tutor")
    assert edge.occurred_at == payload.occurred_at


async def test_replaying_the_same_event_changes_nothing():
    graph = _InMemoryGraph()
    payload = _payload()
    job = ProjectAssessmentAttemptJob(graph)

    await job.handle(payload)
    await job.handle(payload)

    assert graph.writes == 1 and len(graph.edges) == 1


async def test_an_older_event_arriving_late_cannot_alter_a_newer_attempt():
    graph = _InMemoryGraph()
    job = ProjectAssessmentAttemptJob(graph)
    account = uuid4()
    older = _payload(account_id=account, outcome="incorrect",
                     occurred_at=datetime(2026, 9, 18, 9, 0, tzinfo=timezone.utc))
    newer = _payload(account_id=account, outcome="correct",
                     occurred_at=datetime(2026, 9, 18, 10, 0, tzinfo=timezone.utc))

    await job.handle(newer)
    await job.handle(older)  # delayed at-least-once delivery

    assert graph.edges[newer.attempt_id].outcome == "correct"
    assert graph.edges[older.attempt_id].outcome == "incorrect"


async def test_a_missing_concept_is_retried_not_fabricated():
    graph = _InMemoryGraph(concepts=())
    with pytest.raises(ConceptNotProjectedError):
        await ProjectAssessmentAttemptJob(graph).handle(_payload())
    assert graph.edges == {}


def test_payloads_carrying_labels_or_naive_times_are_rejected():
    with pytest.raises(ValidationError):
        _payload(status="demonstrated_recently")
    with pytest.raises(ValidationError):
        _payload(occurred_at=datetime(2026, 9, 18, 10, 0))
    with pytest.raises(ValidationError):
        _payload(outcome="mastered")


def test_the_cypher_never_creates_concepts_or_rewrites_an_existing_attempt():
    statement = " ".join(ATTEMPTED_EDGE_CYPHER.split())
    assert statement.startswith("MATCH (c:Concept {concept_id: $concept_id})")
    assert "MERGE (c" not in statement and "MERGE (:Concept" not in statement
    assert "MERGE (s)-[a:ATTEMPTED {attempt_id: $attempt_id}]->(c)" in statement
    # Properties are only ever set on creation: no unconditional SET.
    assert statement.count(" SET ") == statement.count("ON CREATE SET") == 1
    assert "status" not in statement.lower() and "mastery" not in statement.lower()
