"""Review scheduling policy — deterministic, not the Tutor's judgment call.

Review scheduling works off the same append-only AssessmentAttempt
history and the same four LearningStatus labels as the rest of learning,
never a probabilistic mastery score (learning.md: "Do not invent
probabilities, mastery scores, confidence thresholds or new labels").

The scheduling arithmetic is pure local computation, like
netra_worker.runtime.retries.ExponentialBackoffWithJitter, so it is
implemented in full. The intervals it applies are not: those are product
policy and must be supplied.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Mapping, Optional, Protocol

from pydantic import BaseModel, ConfigDict, Field

from netra_api.learning.assessment.models import LearningStatus


class ReviewIntervalPolicy(BaseModel):
    """Versioned interval table used to schedule the next review.

    intervals has no default, deliberately.

    learning.md: "Derive review scheduling from the approved versioned
    policy" and "Do not turn illustrative handbook intervals or
    thresholds into hidden product policy."

    Earlier scaffold work carried 1/3/7-day intervals as a module-level
    dict. Those were illustrative values standing in for a decision
    nobody had made, and once they were the module default they were
    indistinguishable from an approved schedule. Supplying them is now
    explicit and versioned, so a scheduled review can be traced back to
    the revision that produced it.

    A status absent from intervals has no scheduled review. NOT_ASSESSED
    is expected to be absent: there is nothing to reinforce until a
    concept has been assessed at least once.
    """

    model_config = ConfigDict(frozen=True)

    policy_version: str = Field(min_length=1)
    intervals: Mapping[LearningStatus, timedelta]


class ReviewPolicy(Protocol):
    def next_review_at(
        self, status: LearningStatus, last_assessed_at: datetime
    ) -> Optional[datetime]:
        ...


class FixedIntervalReviewPolicy:
    """next_review_at = last_assessed_at + the configured interval for status.

    Requires an explicit ReviewIntervalPolicy; there is no default
    schedule to fall back on.
    """

    def __init__(self, policy: ReviewIntervalPolicy) -> None:
        self._policy = policy

    @property
    def policy_version(self) -> str:
        return self._policy.policy_version

    def next_review_at(
        self, status: LearningStatus, last_assessed_at: datetime
    ) -> Optional[datetime]:
        interval = self._policy.intervals.get(status)
        if interval is None:
            return None
        return last_assessed_at + interval
