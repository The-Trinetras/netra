"""Review-scheduling background job.

CLAUDE.md "Background jobs": long-running work belongs in worker/, not
HTTP handlers. This job recomputes one account's due reviews from
PostgreSQL assessment history — never a cached mastery score — using
netra_api.learning.review.policy, and persists the result through
netra_api.learning.review.service.ReviewScheduleRepository. Structurally
implements netra_worker.runtime.job_repository.JobHandler. Must not hold
a PostgreSQL transaction open while doing this (CLAUDE.md "Background
jobs"); no persistence is wired up in this scaffold.
"""

from __future__ import annotations

from uuid import UUID

from netra_worker.runtime.job_repository import JobPayload


class ScheduleReviewsPayload(JobPayload):
    """One account's review schedule recompute.

    Scoped to account_id so a single job never covers every account at
    once — worker.jobs.cleanup-style fan-out enqueues one of these per
    account instead.
    """

    account_id: UUID


class ScheduleReviewsJob:
    """Structurally implements
    netra_worker.runtime.job_repository.JobHandler[ScheduleReviewsPayload].

    TODO: inject netra_api.learning.assessment.repository.AssessmentHistoryRepository
    and netra_api.learning.review.policy.ReviewPolicy (via
    netra_api.learning.review.service.ReviewSchedulingService) once
    worker-side service wiring is decided. No persistence call is
    implemented here.
    """

    async def handle(self, payload: ScheduleReviewsPayload) -> None:
        raise NotImplementedError("TODO: schedule_reviews — no persistence wired up")
