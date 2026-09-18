"""Factual learning-graph projection payload, produced from a committed attempt.

D4 (docs/team/handoffs/M4.md). The outbox event written alongside a
committed AssessmentAttempt must carry every fact the worker needs to
project it, because the worker has no AuthContext and must not re-read
account-scoped state. This module builds that payload from the committed
record only — never from a proposal, model output or client input.

The consuming shape is
netra_worker.jobs.learning_projection.neo4j.ProjectAssessmentAttemptPayload.
The worker imports nothing from netra_api at runtime, so the two are kept
in step by a cross-package test rather than a shared import.

Writing this payload in the SAME transaction as the attempt needs M2's
outbox port callable from netra_api (today OutboxRepository lives in
netra_worker.runtime). Until that reviewed port exists nothing enqueues
it; LearningService.propose_event is unchanged. No mastery label or
aggregate appears here: one committed attempt, its recorded facts.
"""

from __future__ import annotations

from typing import Any

from netra_api.learning.assessment.models import AssessmentAttempt

LEARNING_PROJECTION_JOB_TYPE = "learning.project_assessment_attempt"
LEARNING_PROJECTION_AGGREGATE_TYPE = "assessment_attempt"


def projection_idempotency_key(attempt: AssessmentAttempt) -> str:
    """One projection job per committed attempt, however often it is enqueued."""

    return f"{LEARNING_PROJECTION_JOB_TYPE}:{attempt.attempt_id}"


def attempt_projection_payload(attempt: AssessmentAttempt) -> dict[str, Any]:
    """JSON-ready payload for the factual projection of one committed attempt."""

    if attempt.created_at.tzinfo is None or attempt.created_at.utcoffset() is None:
        raise ValueError("a committed attempt's created_at must be timezone-aware")
    return {
        "idempotency_key": projection_idempotency_key(attempt),
        "account_id": str(attempt.account_id),
        "attempt_id": str(attempt.attempt_id),
        "concept_id": attempt.concept_id,
        "outcome": attempt.outcome.value,
        "hints_used": attempt.hints_used,
        "evaluated_by": attempt.evaluated_by,
        "occurred_at": attempt.created_at.isoformat(),
    }
