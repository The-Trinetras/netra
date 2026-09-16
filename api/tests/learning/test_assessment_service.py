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
    QuestionNotPendingError,
    QuestionVersionMismatchError,
    StatusDerivationPolicy,
    UnrepresentableLearningEventError,
    derive_status_from_history,
)
from netra_api.learning.quiz.models import AnswerKey, ApprovedQuestion, QuestionKind
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
        self.append_calls = 0

    def append(self, auth, attempt):
        self.append_calls += 1
        existing = next((a for a in self._attempts if a.attempt_id == attempt.attempt_id), None)
        if existing is not None:
            return existing
        self._attempts.append(attempt)
        return attempt

    def list_for_concept(self, auth, concept_id):
        return [a for a in self._attempts if a.concept_id == concept_id]

    def list_for_account(self, auth):
        return list(self._attempts)

    def get(self, auth, attempt_id):
        raise NotImplementedError


def _pending_question(**overrides):
    defaults = dict(
        question_id="q-1",
        question_version=1,
        concept_id="concept-x",
        kind=QuestionKind.TRUE_FALSE,
        prompt="Is TCP connection-oriented?",
        options=[],
        answer_key=AnswerKey(correct_answer="true"),
        created_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return ApprovedQuestion(**defaults)


class _FakePendingQuestionRepository:
    """Mirrors PendingQuestionRepository: pending until mark_answered clears it.

    Deliberately does not scope by account — matching the interface,
    which relies on the concrete implementation's storage to key pending
    questions by (account_id, question_id); this fake only needs to model
    the pending/answered state transition propose_event depends on.
    """

    def __init__(self, pending=()):
        self._pending = {q.question_id: q for q in pending}
        self._answered: set[str] = set()

    def persist_pending(self, auth, question):
        self._pending[question.question_id] = question
        self._answered.discard(question.question_id)
        return question

    def get_pending(self, auth, question_id):
        if question_id in self._answered:
            return None
        return self._pending.get(question_id)

    def mark_answered(self, auth, question_id, question_version):
        self._answered.add(question_id)


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


def _service(attempts=(), pending=()):
    return LearningService(_FakeRepository(attempts), _FIXTURE_POLICY, _FakePendingQuestionRepository(pending))


def _answer_proposal(account_id, **overrides):
    defaults = dict(
        account_id=account_id,
        concept_id="concept-x",
        event_type="answer_evaluated",
        question_id="q-1",
        question_version=1,
        answer=AnswerSubmission(final_text="true"),
        outcome=AttemptOutcome.CORRECT,
        evaluated_by="grader",
    )
    defaults.update(overrides)
    return LearningEventProposal(**defaults)


def test_propose_event_rejects_cross_account_proposal():
    service = _service()
    auth = _auth(uuid4())
    proposal = LearningEventProposal(account_id=uuid4(), concept_id="concept-x", event_type="concept_exposed")
    with pytest.raises(AuthorizationError):
        service.propose_event(auth, proposal)


def test_propose_event_rejects_incomplete_answer_evaluated_proposal():
    account_id = uuid4()
    service = _service()
    auth = _auth(account_id)
    proposal = LearningEventProposal(account_id=account_id, concept_id="concept-x", event_type="answer_evaluated")
    with pytest.raises(InvalidLearningEventProposalError):
        service.propose_event(auth, proposal)


def test_propose_event_rejects_event_types_assessment_attempt_cannot_represent():
    """concept_exposed/hint_used/review_requested have no AssessmentAttempt
    representation yet (question_id/answer/outcome are all required) — see
    docs/team/handoffs/M4.md."""

    account_id = uuid4()
    service = _service()
    auth = _auth(account_id)
    proposal = LearningEventProposal(account_id=account_id, concept_id="concept-x", event_type="concept_exposed")
    with pytest.raises(UnrepresentableLearningEventError):
        service.propose_event(auth, proposal)


def test_propose_event_commits_a_pending_answer_evaluated_proposal():
    account_id = uuid4()
    service = _service(pending=[_pending_question()])
    auth = _auth(account_id)
    proposal = _answer_proposal(account_id)

    committed = service.propose_event(auth, proposal)

    assert committed.question_id == "q-1"
    assert committed.outcome == AttemptOutcome.CORRECT
    assert committed.evaluated_by == "grader"
    # Finality: the question is no longer pending once answered.
    assert service._pending_question_repository.get_pending(auth, "q-1") is None


def test_propose_event_rejects_a_question_that_is_not_pending():
    account_id = uuid4()
    service = _service(pending=[])  # nothing persisted as pending
    auth = _auth(account_id)
    proposal = _answer_proposal(account_id)
    with pytest.raises(QuestionNotPendingError):
        service.propose_event(auth, proposal)


def test_propose_event_rejects_answering_an_already_answered_question():
    account_id = uuid4()
    service = _service(pending=[_pending_question()])
    auth = _auth(account_id)
    proposal = _answer_proposal(account_id)

    service.propose_event(auth, proposal)  # first, legitimate answer

    with pytest.raises(QuestionNotPendingError):
        service.propose_event(auth, proposal)


def test_propose_event_rejects_a_stale_question_version():
    account_id = uuid4()
    service = _service(pending=[_pending_question(question_version=2)])
    auth = _auth(account_id)
    proposal = _answer_proposal(account_id, question_version=1)
    with pytest.raises(QuestionVersionMismatchError):
        service.propose_event(auth, proposal)


def test_propose_event_is_replay_safe_for_a_retried_request_id():
    """A retried turn.submit reuses the same request_id (message-flow.md);
    propose_event must not append a second attempt for it."""

    account_id = uuid4()
    repository = _FakeRepository([])
    pending_repo = _FakePendingQuestionRepository([_pending_question()])
    service = LearningService(repository, _FIXTURE_POLICY, pending_repo)

    request_id = uuid4()
    auth = AuthContext(account_id=account_id, session_id=uuid4(), request_id=request_id, issued_at=datetime.now(timezone.utc))
    proposal = _answer_proposal(account_id)

    first = service.propose_event(auth, proposal)
    # Re-mark pending as though the client's retry arrived before the
    # first response did (transport-level replay, not a new question).
    pending_repo._pending[proposal.question_id] = _pending_question()
    pending_repo._answered.discard(proposal.question_id)
    second = service.propose_event(auth, proposal)

    assert first.attempt_id == second.attempt_id
    assert repository.append_calls == 2  # both calls reach the repository
    assert len(repository.list_for_account(auth)) == 1  # but only one attempt is stored


def test_derive_current_status_reports_attempt_count():
    account_id = uuid4()
    now = datetime.now(timezone.utc)
    attempts = [_attempt(AttemptOutcome.CORRECT, now, concept_id="concept-x", account_id=account_id)]
    service = _service(attempts=attempts)
    auth = _auth(account_id)
    result = service.derive_current_status(auth, "concept-x")
    assert result.based_on_attempts == 1
    assert result.status == LearningStatus.DEVELOPING
