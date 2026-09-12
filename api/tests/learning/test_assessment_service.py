from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from netra_api.learning.assessment.models import (
    AnswerSubmission,
    AssessmentAttempt,
    AttemptOutcome,
    LearningEventProposal,
    LearningStatus,
)
from netra_api.learning.assessment.service import (
    InvalidLearningEventProposalError,
    LearningService,
    StatusDerivationPolicy,
    derive_status_from_history,
)
from netra_api.platform.auth_context import AuthContext
from netra_api.platform.errors import AuthorizationError

# Arbitrary thresholds chosen to make the derivation checkable. These are a
# TEST FIXTURE, not Netra's derivation policy: the real window and streak
# are an unmade product decision (learning.md: "If derivation/scheduling
# policy is unspecified, report it and leave an explicit stub"). Nothing
# outside this module may import them.
_FIXTURE_POLICY = StatusDerivationPolicy(
    policy_version="test-fixture",
    recent_window=timedelta(days=7),
    recent_correct_required=2,
)


def _attempt(outcome: AttemptOutcome, created_at: datetime, **overrides):
    defaults = dict(
        attempt_id=uuid4(),
        account_id=uuid4(),
        concept_id="concept-x",
        question_id="q-1",
        question_version=1,
        answer=AnswerSubmission(final_text="an answer"),
        outcome=outcome,
        evaluated_by="grader",
        created_at=created_at,
    )
    defaults.update(overrides)
    return AssessmentAttempt(**defaults)


def _auth(account_id):
    return AuthContext(account_id=account_id, session_id=uuid4(), request_id=uuid4(), issued_at=datetime.now(timezone.utc))


class _FakeRepository:
    def __init__(self, attempts):
        self._attempts = list(attempts)

    def append(self, auth, attempt):
        self._attempts.append(attempt)
        return attempt

    def list_for_concept(self, auth, concept_id):
        return [a for a in self._attempts if a.concept_id == concept_id]

    def list_for_account(self, auth):
        return list(self._attempts)

    def get(self, auth, attempt_id):
        raise NotImplementedError


def test_derive_status_with_no_history_is_not_assessed():
    assert derive_status_from_history([], _FIXTURE_POLICY) == LearningStatus.NOT_ASSESSED


def test_derive_status_demonstrated_recently_needs_two_recent_correct():
    now = datetime.now(timezone.utc)
    attempts = [
        _attempt(AttemptOutcome.CORRECT, now - timedelta(days=2)),
        _attempt(AttemptOutcome.CORRECT, now - timedelta(hours=1)),
    ]
    assert derive_status_from_history(attempts, _FIXTURE_POLICY, now=now) == LearningStatus.DEMONSTRATED_RECENTLY


def test_derive_status_single_recent_correct_is_still_developing():
    now = datetime.now(timezone.utc)
    attempts = [_attempt(AttemptOutcome.CORRECT, now - timedelta(hours=1))]
    assert derive_status_from_history(attempts, _FIXTURE_POLICY, now=now) == LearningStatus.DEVELOPING


def test_derive_status_needs_review_after_latest_incorrect():
    now = datetime.now(timezone.utc)
    attempts = [
        _attempt(AttemptOutcome.CORRECT, now - timedelta(days=1)),
        _attempt(AttemptOutcome.CORRECT, now - timedelta(hours=5)),
        _attempt(AttemptOutcome.INCORRECT, now - timedelta(hours=1)),
    ]
    assert derive_status_from_history(attempts, _FIXTURE_POLICY, now=now) == LearningStatus.NEEDS_REVIEW


def test_derive_status_ignores_stale_correct_attempts():
    now = datetime.now(timezone.utc)
    attempts = [
        _attempt(AttemptOutcome.CORRECT, now - timedelta(days=30)),
        _attempt(AttemptOutcome.CORRECT, now - timedelta(days=29)),
    ]
    assert derive_status_from_history(attempts, _FIXTURE_POLICY, now=now) == LearningStatus.DEVELOPING


def test_propose_event_rejects_cross_account_proposal():
    service = LearningService(_FakeRepository([]), _FIXTURE_POLICY)
    auth = _auth(uuid4())
    proposal = LearningEventProposal(account_id=uuid4(), concept_id="concept-x", event_type="concept_exposed")
    with pytest.raises(AuthorizationError):
        service.propose_event(auth, proposal)


def test_propose_event_rejects_incomplete_answer_evaluated_proposal():
    account_id = uuid4()
    service = LearningService(_FakeRepository([]), _FIXTURE_POLICY)
    auth = _auth(account_id)
    proposal = LearningEventProposal(account_id=account_id, concept_id="concept-x", event_type="answer_evaluated")
    with pytest.raises(InvalidLearningEventProposalError):
        service.propose_event(auth, proposal)


def test_propose_event_not_yet_implemented_for_valid_proposal():
    account_id = uuid4()
    service = LearningService(_FakeRepository([]), _FIXTURE_POLICY)
    auth = _auth(account_id)
    proposal = LearningEventProposal(account_id=account_id, concept_id="concept-x", event_type="concept_exposed")
    with pytest.raises(NotImplementedError):
        service.propose_event(auth, proposal)


def test_derive_current_status_reports_attempt_count():
    account_id = uuid4()
    now = datetime.now(timezone.utc)
    attempts = [_attempt(AttemptOutcome.CORRECT, now, concept_id="concept-x", account_id=account_id)]
    service = LearningService(_FakeRepository(attempts), _FIXTURE_POLICY)
    auth = _auth(account_id)
    result = service.derive_current_status(auth, "concept-x")
    assert result.based_on_attempts == 1
    assert result.status == LearningStatus.DEVELOPING
