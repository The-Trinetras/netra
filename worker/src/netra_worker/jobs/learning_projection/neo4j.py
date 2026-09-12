"""Learning-graph projection background job.

Drains netra_worker.runtime.outbox events enqueued alongside a
committed AssessmentAttempt and projects them into Neo4j via
netra_api.learning.graph.projection.LearningGraphProjector (CLAUDE.md
"Background jobs": "Use an outbox when a committed PostgreSQL mutation
requires a later projection/update"; "A failed Neo4j write must never
roll back an already committed assessment attempt" — a failure here
must only retry this job, never touch the PostgreSQL assessment
record). Structurally implements
netra_worker.runtime.job_repository.JobHandler. No Neo4j driver call is
implemented here.
"""

from __future__ import annotations

from uuid import UUID

from netra_worker.runtime.job_repository import JobPayload


class ProjectAssessmentAttemptPayload(JobPayload):
    account_id: UUID
    attempt_id: UUID


class ProjectAssessmentAttemptJob:
    """Structurally implements
    netra_worker.runtime.job_repository.JobHandler[ProjectAssessmentAttemptPayload].

    TODO: inject netra_api.learning.graph.projection.LearningGraphProjector
    once worker-side provider wiring is decided. No Neo4j driver call is
    implemented here.
    """

    async def handle(self, payload: ProjectAssessmentAttemptPayload) -> None:
        raise NotImplementedError("TODO: project_assessment_attempt — no Neo4j projection wired up")
