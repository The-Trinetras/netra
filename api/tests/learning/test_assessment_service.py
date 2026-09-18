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
        """Mirrors the Protocol: None when unknown, AuthorizationError when
        the attempt exists but belongs to another account."""

        found = next((a for a in self._attempts if a.attempt_id == attempt_id), None)
        if found is None:
            return None
        if found.account_id != auth.account_id:
            raise AuthorizationError("attempt belongs to another account")
        return found


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


async def test_propose_event_rejects_cross_account_proposal():
    service = _service()
    auth = _auth(uuid4())
    proposal = LearningEventProposal(account_id=uuid4(), concept_id="concept-x", event_type="concept_exposed")
    with pytest.raises(AuthorizationError):
        await service.propose_event(auth, proposal)


async def test_propose_event_rejects_incomplete_answer_evaluated_proposal():
    account_id = uuid4()
    service = _service()
    auth = _auth(account_id)
    proposal = LearningEventProposal(account_id=account_id, concept_id="concept-x", event_type="answer_evaluated")
    with pytest.raises(InvalidLearningEventProposalError):
        await service.propose_event(auth, proposal)


async def test_propose_event_rejects_event_types_assessment_attempt_cannot_represent():
    """concept_exposed/hint_used/review_requested have no AssessmentAttempt
    representation yet (question_id/answer/outcome are all required) — see
    docs/team/handoffs/M4.md."""

    account_id = uuid4()
    service = _service()
    auth = _auth(account_id)
    proposal = LearningEventProposal(account_id=account_id, concept_id="concept-x", event_type="concept_exposed")
    with pytest.raises(UnrepresentableLearningEventError):
        await service.propose_event(auth, proposal)


async def test_propose_event_commits_a_pending_answer_evaluated_proposal():
    account_id = uuid4()
    service = _service(pending=[_pending_question()])
    auth = _auth(account_id)
    proposal = _answer_proposal(account_id)

    committed = await service.propose_event(auth, proposal)

    assert committed.question_id == "q-1"
    assert committed.outcome == AttemptOutcome.CORRECT
    assert committed.evaluated_by == "grader"
    # Finality: the question is no longer pending once answered.
    assert service._pending_question_repository.get_pending(auth, "q-1") is None


async def test_propose_event_rejects_a_question_that_is_not_pending():
    account_id = uuid4()
    service = _service(pending=[])  # nothing persisted as pending
    auth = _auth(account_id)
    proposal = _answer_proposal(account_id)
    with pytest.raises(QuestionNotPendingError):
        await service.propose_event(auth, proposal)


async def test_propose_event_rejects_a_new_turn_answering_an_already_answered_question():
    """A *different* turn (new request_id) answering a question that is no
    longer pending is a genuine finality violation, not a replay — the
    replay path only covers retransmission of the same logical action."""

    account_id = uuid4()
    service = _service(pending=[_pending_question()])
    first_auth = _auth(account_id)
    proposal = _answer_proposal(account_id)

    await service.propose_event(first_auth, proposal)  # first, legitimate answer

    later_turn = AuthContext(
        account_id=account_id,
        session_id=first_auth.session_id,
        request_id=uuid4(),
        issued_at=datetime.now(timezone.utc),
    )
    with pytest.raises(QuestionNotPendingError):
        await service.propose_event(later_turn, proposal)


async def test_propose_event_replays_a_duplicate_submission_without_a_second_commit():
    """The service itself is replay-safe: a duplicate delivery of the same
    turn returns the original attempt rather than erroring on finality or
    appending a second record."""

    account_id = uuid4()
    repository = _FakeRepository([])
    service = LearningService(repository, _FIXTURE_POLICY, _FakePendingQuestionRepository([_pending_question()]))
    auth = _auth(account_id)
    proposal = _answer_proposal(account_id)

    first = await service.propose_event(auth, proposal)
    second = await service.propose_event(auth, proposal)  # duplicate delivery

    assert first.attempt_id == second.attempt_id
    assert len(repository.list_for_account(auth)) == 1


async def test_propose_event_rejects_a_stale_question_version():
    account_id = uuid4()
    service = _service(pending=[_pending_question(question_version=2)])
    auth = _auth(account_id)
    proposal = _answer_proposal(account_id, question_version=1)
    with pytest.raises(QuestionVersionMismatchError):
        await service.propose_event(auth, proposal)


async def test_propose_event_is_replay_safe_for_a_retried_request_id():
    """A retried turn.submit reuses the same request_id (message-flow.md);
    propose_event must not append a second attempt for it."""

    account_id = uuid4()
    repository = _FakeRepository([])
    pending_repo = _FakePendingQuestionRepository([_pending_question()])
    service = LearningService(repository, _FIXTURE_POLICY, pending_repo)

    request_id = uuid4()
    auth = AuthContext(account_id=account_id, session_id=uuid4(), request_id=request_id, issued_at=datetime.now(timezone.utc))
    proposal = _answer_proposal(account_id)

    first = await service.propose_event(auth, proposal)
    second = await service.propose_event(auth, proposal)  # retransmitted turn

    assert first.attempt_id == second.attempt_id
    # The replay short-circuits before the write path entirely, so the
    # duplicate never reaches append at all — idempotency here does not
    # depend on the repository recognising a repeated attempt_id.
    assert repository.append_calls == 1
    assert len(repository.list_for_account(auth)) == 1


async def test_find_committed_attempt_returns_none_before_anything_is_committed():
    account_id = uuid4()
    service = _service(pending=[_pending_question()])
    assert await service.find_committed_attempt(_auth(account_id), "q-1", 1) is None


async def test_find_committed_attempt_finds_this_turns_own_attempt():
    """The replay lookup that stops a retransmission from looking like a
    failure once propose_event has cleared the pending question."""

    account_id = uuid4()
    service = _service(pending=[_pending_question()])
    auth = _auth(account_id)

    committed = await service.propose_event(auth, _answer_proposal(account_id))

    found = await service.find_committed_attempt(auth, "q-1", 1)
    assert found is not None
    assert found.attempt_id == committed.attempt_id


async def test_find_committed_attempt_does_not_match_a_different_turn():
    """A different request_id is a different logical action, so it is a
    genuinely new attempt rather than a replay of the previous one."""

    account_id = uuid4()
    service = _service(pending=[_pending_question()])
    first_auth = _auth(account_id)
    await service.propose_event(first_auth, _answer_proposal(account_id))

    other_turn = AuthContext(
        account_id=account_id,
        session_id=first_auth.session_id,
        request_id=uuid4(),
        issued_at=datetime.now(timezone.utc),
    )
    assert await service.find_committed_attempt(other_turn, "q-1", 1) is None


async def test_list_attempts_for_concepts_returns_facts_in_time_order():
    account_id = uuid4()
    now = datetime.now(timezone.utc)
    older = _attempt(AttemptOutcome.INCORRECT, now - timedelta(days=2), concept_id="c-1", account_id=account_id)
    newer = _attempt(AttemptOutcome.CORRECT, now, concept_id="c-2", account_id=account_id)
    service = _service(attempts=[newer, older])

    listed = await service.list_attempts_for_concepts(_auth(account_id), ["c-1", "c-2"])

    assert [a.attempt_id for a in listed] == [older.attempt_id, newer.attempt_id]
    # Facts only: no derived label is attached to the returned records.
    assert all(not hasattr(a, "status") for a in listed)


async def test_derive_current_status_reports_attempt_count():
    account_id = uuid4()
    now = datetime.now(timezone.utc)
    attempts = [_attempt(AttemptOutcome.CORRECT, now, concept_id="concept-x", account_id=account_id)]
    service = _service(attempts=attempts)
    auth = _auth(account_id)
    result = await service.derive_current_status(auth, "concept-x")
    assert result.based_on_attempts == 1
    assert result.status == LearningStatus.DEVELOPING
