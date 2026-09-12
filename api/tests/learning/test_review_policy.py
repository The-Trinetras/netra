from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from netra_api.learning.assessment.models import LearningStatus
from netra_api.learning.review.policy import FixedIntervalReviewPolicy, ReviewIntervalPolicy

# Arbitrary intervals chosen to make the arithmetic checkable. These are a
# TEST FIXTURE, not Netra's review schedule: the real intervals are an
# unmade product decision (learning.md: "Do not turn illustrative handbook
# intervals or thresholds into hidden product policy"). Nothing outside
# this module may import them.
_FIXTURE_POLICY = ReviewIntervalPolicy(
    policy_version="test-fixture",
    intervals={
        LearningStatus.NEEDS_REVIEW: timedelta(days=2),
        LearningStatus.DEVELOPING: timedelta(days=5),
        LearningStatus.DEMONSTRATED_RECENTLY: timedelta(days=11),
    },
)


def test_policy_requires_explicit_intervals():
    """There is no default schedule to fall back on, by design."""

    with pytest.raises(ValidationError):
        ReviewIntervalPolicy(policy_version="missing-intervals")


def test_status_absent_from_intervals_has_no_next_review():
    policy = FixedIntervalReviewPolicy(_FIXTURE_POLICY)
    assert policy.next_review_at(LearningStatus.NOT_ASSESSED, datetime.now(timezone.utc)) is None


def test_next_review_applies_the_configured_interval():
    policy = FixedIntervalReviewPolicy(_FIXTURE_POLICY)
    last_assessed_at = datetime.now(timezone.utc)
    next_at = policy.next_review_at(LearningStatus.NEEDS_REVIEW, last_assessed_at)
    assert next_at == last_assessed_at + timedelta(days=2)


def test_policy_version_is_reported_for_audit():
    policy = FixedIntervalReviewPolicy(_FIXTURE_POLICY)
    assert policy.policy_version == "test-fixture"
