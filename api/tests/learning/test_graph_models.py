from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from netra_api.learning.assessment.models import LearningStatus
from netra_api.learning.graph.neo4j import ConceptNode, MasteryEdge, PrerequisiteEdge


def _mastery_edge(**overrides):
    defaults = dict(
        account_id=uuid4(),
        concept_id="concept-x",
        status=LearningStatus.DEVELOPING,
        observed_at=datetime.now(timezone.utc),
        source_attempt_id=uuid4(),
    )
    defaults.update(overrides)
    return MasteryEdge(**defaults)


def test_concept_node_round_trips_fields():
    node = ConceptNode(concept_id="concept-x", label="Congestion control")
    assert node.concept_id == "concept-x"
    assert node.label == "Congestion control"


def test_prerequisite_edge_holds_both_concept_ids():
    edge = PrerequisiteEdge(concept_id="concept-y", prerequisite_concept_id="concept-x")
    assert edge.prerequisite_concept_id == "concept-x"


def test_mastery_edge_status_mirrors_learning_status():
    edge = _mastery_edge()
    assert edge.status == LearningStatus.DEVELOPING
    assert edge.last_assessed_at is None


def test_mastery_edge_requires_an_ordering_key():
    """observed_at has no default: an unordered projection could let a
    replayed older event overwrite newer state."""

    with pytest.raises(ValidationError):
        MasteryEdge(
            account_id=uuid4(),
            concept_id="concept-x",
            status=LearningStatus.DEVELOPING,
            source_attempt_id=uuid4(),
        )


def test_mastery_edges_are_comparable_by_observed_at():
    """The projection's newest-wins guard needs edges to be orderable."""

    now = datetime.now(timezone.utc)
    older = _mastery_edge(observed_at=now - timedelta(hours=1))
    newer = _mastery_edge(observed_at=now)
    assert older.observed_at < newer.observed_at
