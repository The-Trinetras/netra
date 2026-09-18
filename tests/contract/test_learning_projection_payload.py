"""Producer/consumer contract for the factual learning-graph projection (D4).

The worker imports nothing from netra_api at runtime, so the payload the
API builds from a committed attempt and the payload the worker accepts are
two separate definitions. This cross-package test is what keeps them in
step until a reviewed shared schema under shared/contracts/jobs/v1/
replaces it (M2/M4 decision). It does not prove the outbox write happens
in the attempt's transaction; that needs M2's port and a test database.
"""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from netra_api.learning.assessment.models import AnswerSubmission, AssessmentAttempt, AttemptOutcome
from netra_api.learning.graph.factual import (
    LEARNING_PROJECTION_JOB_TYPE as API_JOB_TYPE,
    attempt_projection_payload,
)
from netra_worker.jobs.learning_projection.neo4j import (
    LEARNING_PROJECTION_JOB_TYPE as WORKER_JOB_TYPE,
    ProjectAssessmentAttemptPayload,
)


def _attempt(**overrides):
    defaults = dict(
        attempt_id=uuid4(),
        account_id=uuid4(),
        concept_id="concept-ohms-law",
        question_id="q-1",
        question_version=1,
        answer=AnswerSubmission(final_text="PRIVATE ANSWER TEXT"),
        outcome=AttemptOutcome.PARTIAL,
        hints_used=1,
        evaluated_by="grader",
        created_at=datetime(2026, 9, 18, 10, 0, tzinfo=timezone.utc),
    )
    defaults.update(overrides)
    return AssessmentAttempt(**defaults)


def test_the_api_payload_is_exactly_what_the_worker_accepts():
    attempt = _attempt()
    payload = ProjectAssessmentAttemptPayload.model_validate(attempt_projection_payload(attempt))

    assert API_JOB_TYPE == WORKER_JOB_TYPE
    assert payload.attempt_id == attempt.attempt_id
    assert payload.account_id == attempt.account_id
    assert (payload.outcome, payload.hints_used, payload.evaluated_by) == ("partial", 1, "grader")
    assert payload.occurred_at == attempt.created_at
    assert payload.idempotency_key.endswith(str(attempt.attempt_id))


def test_every_attempt_outcome_is_representable_by_the_worker():
    for outcome in AttemptOutcome:
        raw = attempt_projection_payload(_attempt(outcome=outcome))
        assert ProjectAssessmentAttemptPayload.model_validate(raw).outcome == outcome.value


def test_the_payload_carries_facts_not_answer_text_or_any_label():
    raw = attempt_projection_payload(_attempt())
    assert "PRIVATE ANSWER TEXT" not in str(raw)
    assert not {"status", "mastery", "answer", "question_id"} & set(raw)


def test_a_naive_commit_time_is_refused_at_the_producer():
    with pytest.raises(ValueError):
        attempt_projection_payload(_attempt(created_at=datetime(2026, 9, 18, 10, 0)))
