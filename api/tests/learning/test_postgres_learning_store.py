"""Durable learning persistence on the disposable PostgreSQL (INT-08).

Proves the reviewed atomic semantics with real transactions: closing the
pending question, appending the attempt and writing the projection outbox
event commit together or not at all; concurrent answers cannot both commit;
retransmissions replay without new effects; accounts stay isolated.
"""

import asyncio
import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select

from netra_api.db.models import AssessmentAttemptRow, OutboxRow, PendingQuestionRow
from netra_api.db.outbox import LEARNING_ATTEMPT_COMMITTED, TransactionalOutbox
from netra_api.learning.assessment.models import AnswerSubmission, AssessmentAttempt, AttemptOutcome, LearningEventProposal
from netra_api.learning.assessment.service import LearningService, QuestionNotPendingError, derive_attempt_id
from netra_api.learning.postgres import PostgresLearningStore, QuestionIdentityConflictError
from netra_api.learning.quiz.models import AnswerKey, ApprovedQuestion, QuestionKind, QuestionOption
from netra_api.platform.auth_context import AuthContext
from netra_api.platform.database import create_engine, create_session_factory
from netra_api.platform.errors import AuthorizationError

pytestmark = pytest.mark.integration


@pytest.fixture
async def factory():
    url = os.environ.get("NETRA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("NETRA_TEST_DATABASE_URL (a disposable local database) is not set")
    engine = create_engine(url)
    sessions = create_session_factory(engine)
    created: list[str] = []
    yield sessions, created
    async with sessions() as session, session.begin():
        await session.execute(delete(OutboxRow).where(OutboxRow.aggregate_id.in_(
            select(AssessmentAttemptRow.attempt_id).where(AssessmentAttemptRow.question_id.in_(created)))))
        await session.execute(delete(AssessmentAttemptRow).where(AssessmentAttemptRow.question_id.in_(created)))
        await session.execute(delete(PendingQuestionRow).where(PendingQuestionRow.question_id.in_(created)))
    await engine.dispose()


def _auth(account_id, request_id=None):
    return AuthContext(account_id=account_id, session_id=uuid4(), request_id=request_id or uuid4(),
                       issued_at=datetime.now(timezone.utc))


def _question(created, question_id=None):
    question_id = question_id or f"q-{uuid4()}"
    created.append(question_id)
    return ApprovedQuestion(
        question_id=question_id, question_version=1, concept_id="ohms-law", kind=QuestionKind.MULTIPLE_CHOICE,
        prompt="What voltage corresponds to 4 amperes?",
        options=[QuestionOption(option_id="a", text="4 volts"), QuestionOption(option_id="b", text="8 volts")],
        answer_key=AnswerKey(correct_answer="b", rubric="private rubric"), created_at=datetime.now(timezone.utc))


def _attempt(auth, question, outcome=AttemptOutcome.CORRECT):
    return AssessmentAttempt(
        attempt_id=derive_attempt_id(auth.request_id, question.question_id, question.question_version),
        account_id=auth.account_id, concept_id=question.concept_id, question_id=question.question_id,
        question_version=question.question_version, answer=AnswerSubmission(final_text="8 volts"),
        outcome=outcome, hints_used=1, evaluated_by="grader", created_at=datetime.now(timezone.utc))


async def _counts(sessions, question):
    async with sessions() as session:
        attempts = (await session.execute(select(func.count()).select_from(AssessmentAttemptRow).where(
            AssessmentAttemptRow.question_id == question.question_id))).scalar_one()
        events = (await session.execute(select(OutboxRow).where(
            OutboxRow.event_type == LEARNING_ATTEMPT_COMMITTED,
            OutboxRow.aggregate_id.in_(select(AssessmentAttemptRow.attempt_id).where(
                AssessmentAttemptRow.question_id == question.question_id))))).scalars().all()
        answered = (await session.get(PendingQuestionRow, (question.question_id, question.question_version))).answered_at
    return attempts, events, answered


async def test_attempt_question_closure_and_outbox_event_commit_together(factory):
    sessions, created = factory
    store = PostgresLearningStore(sessions)
    auth = _auth(uuid4())
    question = await store.persist_pending(auth, _question(created))
    assert (await store.get_pending(auth, question.question_id)).answer_key.rubric == "private rubric"

    committed = await store.commit_answer(auth, _attempt(auth, question))

    attempts, events, answered = await _counts(sessions, question)
    assert attempts == 1 and answered is not None and len(events) == 1
    payload = events[0].payload
    assert payload["attempt_id"] == str(committed.attempt_id) and payload["outcome"] == "correct"
    assert "8 volts" not in str(payload) and "rubric" not in str(payload)  # facts only, no private text
    assert await store.get_pending(auth, question.question_id) is None
    assert (await store.get(auth, committed.attempt_id)).answer.final_text == "8 volts"


async def test_a_replayed_attempt_returns_the_original_and_adds_nothing(factory):
    sessions, created = factory
    store = PostgresLearningStore(sessions)
    auth = _auth(uuid4())
    question = await store.persist_pending(auth, _question(created))
    first = await store.commit_answer(auth, _attempt(auth, question))
    replay = await store.commit_answer(auth, _attempt(auth, question, outcome=AttemptOutcome.INCORRECT))
    assert replay == first
    attempts, events, _ = await _counts(sessions, question)
    assert attempts == 1 and len(events) == 1


async def test_concurrent_answers_to_one_question_commit_exactly_one(factory):
    sessions, created = factory
    store = PostgresLearningStore(sessions)
    account = uuid4()
    question = await store.persist_pending(_auth(account), _question(created))
    turns = [_auth(account) for _ in range(4)]  # four different turns (request ids)
    outcomes = await asyncio.gather(*[store.commit_answer(auth, _attempt(auth, question)) for auth in turns],
                                    return_exceptions=True)
    assert sum(isinstance(o, AssessmentAttempt) for o in outcomes) == 1
    assert sum(isinstance(o, QuestionNotPendingError) for o in outcomes) == 3
    attempts, events, _ = await _counts(sessions, question)
    assert attempts == 1 and len(events) == 1


async def test_concurrent_retransmissions_of_one_turn_both_see_the_committed_attempt(factory):
    sessions, created = factory
    store = PostgresLearningStore(sessions)
    auth = _auth(uuid4())
    question = await store.persist_pending(auth, _question(created))
    outcomes = await asyncio.gather(*[store.commit_answer(auth, _attempt(auth, question)) for _ in range(3)])
    assert len({o.attempt_id for o in outcomes}) == 1
    attempts, events, _ = await _counts(sessions, question)
    assert attempts == 1 and len(events) == 1


class _FailingOutbox(TransactionalOutbox):
    async def enqueue(self, session, **kwargs):
        await super().enqueue(session, **kwargs)
        raise RuntimeError("injected failure after the outbox insert")


async def test_a_failure_inside_the_commit_leaves_no_effect(factory):
    sessions, created = factory
    auth = _auth(uuid4())
    question = await PostgresLearningStore(sessions).persist_pending(auth, _question(created))
    with pytest.raises(RuntimeError):
        await PostgresLearningStore(sessions, outbox=_FailingOutbox()).commit_answer(auth, _attempt(auth, question))
    attempts, events, answered = await _counts(sessions, question)
    assert (attempts, len(events), answered) == (0, 0, None)
    # The untouched question can still be answered afterwards.
    await PostgresLearningStore(sessions).commit_answer(auth, _attempt(auth, question))
    assert (await _counts(sessions, question))[0] == 1


async def test_other_accounts_cannot_see_answer_or_read_the_question(factory):
    sessions, created = factory
    store = PostgresLearningStore(sessions)
    owner, stranger = _auth(uuid4()), _auth(uuid4())
    question = await store.persist_pending(owner, _question(created))
    assert await store.get_pending(stranger, question.question_id) is None
    with pytest.raises(AuthorizationError):
        await store.persist_pending(stranger, question)
    with pytest.raises(QuestionNotPendingError):
        await store.commit_answer(stranger, _attempt(stranger, question))
    committed = await store.commit_answer(owner, _attempt(owner, question))
    with pytest.raises(AuthorizationError):
        await store.get(stranger, committed.attempt_id)
    assert await store.list_for_account(stranger) == []


async def test_persisting_a_question_is_idempotent_and_its_content_immutable(factory):
    sessions, created = factory
    store = PostgresLearningStore(sessions)
    auth = _auth(uuid4())
    question = _question(created)
    await store.persist_pending(auth, question)
    assert (await store.persist_pending(auth, question)).prompt == question.prompt
    with pytest.raises(QuestionIdentityConflictError):
        await store.persist_pending(auth, question.model_copy(update={"prompt": "A different question?"}))


async def test_learning_service_commits_through_the_durable_store_and_replays(factory):
    sessions, created = factory
    store = PostgresLearningStore(sessions)
    service = LearningService(store, None, store)
    account = uuid4()
    auth = _auth(account)
    question = await store.persist_pending(auth, _question(created))
    proposal = LearningEventProposal(account_id=account, concept_id="ohms-law", event_type="answer_evaluated",
                                     question_id=question.question_id, question_version=1,
                                     answer=AnswerSubmission(final_text="8 volts"), outcome=AttemptOutcome.CORRECT,
                                     evaluated_by="grader", hints_used=0)
    first = await service.propose_event(auth, proposal)
    replay = await service.propose_event(auth, proposal)  # retransmitted turn, question already closed
    assert replay.attempt_id == first.attempt_id
    with pytest.raises(QuestionNotPendingError):
        await service.propose_event(_auth(account), proposal)  # a later, different turn
    assert [a.attempt_id for a in await service.list_attempts_for_concepts(auth, ["ohms-law"])] == [first.attempt_id]
    attempts, events, _ = await _counts(sessions, question)
    assert attempts == 1 and len(events) == 1
