"""Review scheduling service — the read path for "what's due for review".

Deterministic (see netra_api.learning.review.policy); never invokes an
LLM to decide timing. The actual due-review sweep that recomputes and
persists schedules at scale runs in worker/ (CLAUDE.md "Background
jobs": "Long-running ... work belongs in worker/, not HTTP request
handlers") — see
netra_worker.jobs.review_scheduler.schedule_reviews.ScheduleReviewsJob.
This service is what the Coordinator/Tutor call to check what is due
right now, and what that worker job calls to persist a new schedule
after recomputing it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel

from netra_api.learning.assessment.models import LearningStatus
from netra_api.learning.review.policy import ReviewPolicy
from netra_api.platform.auth_context import AuthContext


class DueReview(BaseModel):
    concept_id: str
    status: LearningStatus
    due_at: datetime


class ReviewScheduleRepository(Protocol):
    """Typed contract for the derived "what's due" projection.

    Rebuildable at any time from AssessmentHistoryRepository +
    ReviewPolicy (CLAUDE.md "Data authority") — never itself
    authoritative.
    """

    def list_due(self, auth: AuthContext, as_of: datetime) -> list[DueReview]:
        ...

    def upsert_schedule(self, auth: AuthContext, concept_id: str, due_at: datetime) -> None:
        ...


class ReviewSchedulingService:
    """Application-facing review-scheduling operations. No SQL here."""

    def __init__(self, repository: ReviewScheduleRepository, policy: ReviewPolicy) -> None:
        self._repository = repository
        self._policy = policy

    def list_due(self, auth: AuthContext, as_of: datetime) -> list[DueReview]:
        return self._repository.list_due(auth, as_of)

    def reschedule(
        self, auth: AuthContext, concept_id: str, status: LearningStatus, last_assessed_at: datetime
    ) -> None:
        """Recompute and persist concept_id's next review time, if any."""

        next_at = self._policy.next_review_at(status, last_assessed_at)
        if next_at is not None:
            self._repository.upsert_schedule(auth, concept_id, next_at)
