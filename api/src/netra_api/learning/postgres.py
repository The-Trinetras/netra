"""PostgreSQL store for pending questions and assessment attempts (INT-08).

Implements PendingQuestionRepository, AssessmentHistoryRepository and
AtomicAnswerCommitter over migration 0008. Each method owns one short
transaction on its own session (no caller transaction is ever joined,
committed or rolled back here), and no external call runs inside it.

``commit_answer`` is the reviewed atomic path: in one transaction it closes
the still-pending question with a conditional UPDATE (so two concurrent
answers to one question cannot both commit), inserts the attempt, and writes
the projection outbox event through M2's TransactionalOutbox. A concurrent
retransmission of the same attempt waits on the question row and then
replays the committed attempt instead of failing.

Private fields (answer_key, rubric, the student's answer text) are stored
because they are canonical records, and never logged or traced here.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from netra_api.db.models import AssessmentAttemptRow, PendingQuestionRow
from netra_api.db.outbox import LEARNING_ATTEMPT_COMMITTED, TransactionalOutbox
from netra_api.learning.assessment.models import AnswerSubmission, AssessmentAttempt, AttemptOutcome
from netra_api.learning.assessment.service import QuestionNotPendingError
from netra_api.learning.graph.factual import attempt_projection_payload
from netra_api.learning.quiz.models import AnswerKey, ApprovedQuestion, QuestionEvidenceRef, QuestionKind, QuestionOption
from netra_api.platform.auth_context import AuthContext
from netra_api.platform.errors import AuthorizationError, NetraError


class QuestionIdentityConflictError(NetraError):
    """A question id/version was re-persisted with different content; stored questions are immutable."""


def _question(row: PendingQuestionRow) -> ApprovedQuestion:
    return ApprovedQuestion(
        question_id=row.question_id,
        question_version=row.question_version,
        concept_id=row.concept_id,
        kind=QuestionKind(row.kind),
        prompt=row.prompt,
        options=[QuestionOption.model_validate(item) for item in row.options],
        answer_key=AnswerKey.model_validate(row.answer_key),
        created_at=row.created_at,
        evidence_refs=[QuestionEvidenceRef.model_validate(item) for item in row.evidence_refs],
    )


def _question_content(question: ApprovedQuestion) -> dict:
    return question.model_dump(mode="json", exclude={"created_at"})


def _attempt(row: AssessmentAttemptRow) -> AssessmentAttempt:
    return AssessmentAttempt(
        attempt_id=row.attempt_id,
        account_id=row.account_id,
        concept_id=row.concept_id,
        question_id=row.question_id,
        question_version=row.question_version,
        answer=AnswerSubmission.model_validate(row.answer),
        outcome=AttemptOutcome(row.outcome),
        hints_used=row.hints_used,
        evaluated_by=row.evaluated_by,
        created_at=row.created_at,
    )


def _attempt_row(attempt: AssessmentAttempt) -> AssessmentAttemptRow:
    return AssessmentAttemptRow(
        attempt_id=attempt.attempt_id,
        account_id=attempt.account_id,
        concept_id=attempt.concept_id,
        question_id=attempt.question_id,
        question_version=attempt.question_version,
        answer=attempt.answer.model_dump(mode="json"),
        outcome=attempt.outcome.value,
        hints_used=attempt.hints_used,
        evaluated_by=attempt.evaluated_by,
        created_at=attempt.created_at,
    )


class PostgresLearningStore:
    """Durable PendingQuestionRepository + AssessmentHistoryRepository + AtomicAnswerCommitter."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], outbox: Optional[TransactionalOutbox] = None) -> None:
        self._sessions = sessions
        self._outbox = outbox or TransactionalOutbox()

    # -- pending questions -------------------------------------------------------

    async def persist_pending(self, auth: AuthContext, question: ApprovedQuestion) -> ApprovedQuestion:
        row = PendingQuestionRow(
            question_id=question.question_id,
            question_version=question.question_version,
            account_id=auth.account_id,
            concept_id=question.concept_id,
            kind=question.kind.value,
            prompt=question.prompt,
            options=[option.model_dump(mode="json") for option in question.options],
            answer_key=question.answer_key.model_dump(mode="json"),
            evidence_refs=[ref.model_dump(mode="json") for ref in question.evidence_refs],
            created_at=question.created_at,
        )
        try:
            async with self._sessions() as session, session.begin():
                session.add(row)
            return question
        except IntegrityError:
            pass  # the id/version already exists: an identical re-persist is idempotent
        async with self._sessions() as session:
            existing = await session.get(PendingQuestionRow, (question.question_id, question.question_version))
        if existing is None:
            raise QuestionIdentityConflictError("question could not be persisted")
        if existing.account_id != auth.account_id:
            raise AuthorizationError("question is not accessible")
        stored = _question(existing)
        if _question_content(stored) != _question_content(question):
            raise QuestionIdentityConflictError("a persisted question id/version cannot change")
        return stored

    async def get_pending(self, auth: AuthContext, question_id: str) -> Optional[ApprovedQuestion]:
        async with self._sessions() as session:
            row = (await session.execute(
                select(PendingQuestionRow)
                .where(PendingQuestionRow.question_id == question_id,
                       PendingQuestionRow.account_id == auth.account_id,
                       PendingQuestionRow.answered_at.is_(None))
                .order_by(PendingQuestionRow.question_version.desc())
                .limit(1))).scalar_one_or_none()
        return _question(row) if row else None

    async def get_question(self, auth: AuthContext, question_id: str, question_version: int) -> Optional[ApprovedQuestion]:
        """Any stored version for this account, answered or not (reconnect reconciliation)."""
        async with self._sessions() as session:
            row = await session.get(PendingQuestionRow, (question_id, question_version))
        if row is None or row.account_id != auth.account_id:
            return None
        return _question(row)

    async def is_answered(self, auth: AuthContext, question_id: str, question_version: int) -> bool:
        async with self._sessions() as session:
            row = await session.get(PendingQuestionRow, (question_id, question_version))
        return row is not None and row.account_id == auth.account_id and row.answered_at is not None

    async def mark_answered(self, auth: AuthContext, question_id: str, question_version: int) -> None:
        async with self._sessions() as session, session.begin():
            result = await session.execute(
                update(PendingQuestionRow)
                .where(PendingQuestionRow.question_id == question_id,
                       PendingQuestionRow.question_version == question_version,
                       PendingQuestionRow.account_id == auth.account_id,
                       PendingQuestionRow.answered_at.is_(None))
                .values(answered_at=datetime.now(timezone.utc)))
            if result.rowcount != 1:
                raise QuestionNotPendingError(question_id)

    # -- assessment history --------------------------------------------------------

    async def append(self, auth: AuthContext, attempt: AssessmentAttempt) -> AssessmentAttempt:
        auth.assert_owns_account(attempt.account_id)
        try:
            async with self._sessions() as session, session.begin():
                session.add(_attempt_row(attempt))
            return attempt
        except IntegrityError:
            existing = await self.get(auth, attempt.attempt_id)
            if existing is None:
                raise
            return existing

    async def get(self, auth: AuthContext, attempt_id: UUID) -> Optional[AssessmentAttempt]:
        async with self._sessions() as session:
            row = await session.get(AssessmentAttemptRow, attempt_id)
        if row is None:
            return None
        if row.account_id != auth.account_id:
            raise AuthorizationError("attempt is not accessible")
        return _attempt(row)

    async def list_for_concept(self, auth: AuthContext, concept_id: str) -> list[AssessmentAttempt]:
        async with self._sessions() as session:
            rows = (await session.execute(
                select(AssessmentAttemptRow)
                .where(AssessmentAttemptRow.account_id == auth.account_id,
                       AssessmentAttemptRow.concept_id == concept_id)
                .order_by(AssessmentAttemptRow.created_at, AssessmentAttemptRow.attempt_id))).scalars().all()
        return [_attempt(row) for row in rows]

    async def list_for_account(self, auth: AuthContext) -> list[AssessmentAttempt]:
        async with self._sessions() as session:
            rows = (await session.execute(
                select(AssessmentAttemptRow)
                .where(AssessmentAttemptRow.account_id == auth.account_id)
                .order_by(AssessmentAttemptRow.created_at, AssessmentAttemptRow.attempt_id))).scalars().all()
        return [_attempt(row) for row in rows]

    # -- atomic answer commit ------------------------------------------------------------

    async def commit_answer(self, auth: AuthContext, attempt: AssessmentAttempt) -> AssessmentAttempt:
        auth.assert_owns_account(attempt.account_id)
        payload = attempt_projection_payload(attempt)
        async with self._sessions() as session, session.begin():
            replayed = await self._replayed(session, auth, attempt.attempt_id)
            if replayed is not None:
                return replayed
            closed = await session.execute(
                update(PendingQuestionRow)
                .where(PendingQuestionRow.question_id == attempt.question_id,
                       PendingQuestionRow.question_version == attempt.question_version,
                       PendingQuestionRow.account_id == auth.account_id,
                       PendingQuestionRow.answered_at.is_(None))
                .values(answered_at=attempt.created_at, answered_attempt_id=attempt.attempt_id))
            if closed.rowcount != 1:
                # A concurrent retransmission of this same attempt may have won
                # the row lock and committed; READ COMMITTED lets this
                # statement see it, so replay rather than refuse.
                replayed = await self._replayed(session, auth, attempt.attempt_id)
                if replayed is not None:
                    return replayed
                raise QuestionNotPendingError(attempt.question_id)
            session.add(_attempt_row(attempt))
            await session.flush()
            await self._outbox.enqueue(session, event_type=LEARNING_ATTEMPT_COMMITTED,
                                       aggregate_id=attempt.attempt_id, payload=payload)
        return attempt

    @staticmethod
    async def _replayed(session: AsyncSession, auth: AuthContext, attempt_id: UUID) -> Optional[AssessmentAttempt]:
        row = (await session.execute(
            select(AssessmentAttemptRow).where(AssessmentAttemptRow.attempt_id == attempt_id)
            .execution_options(populate_existing=True))).scalar_one_or_none()
        if row is None:
            return None
        if row.account_id != auth.account_id:
            raise AuthorizationError("attempt is not accessible")
        return _attempt(row)


__all__ = ["PostgresLearningStore", "QuestionIdentityConflictError"]
