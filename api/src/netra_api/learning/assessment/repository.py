"""Assessment-attempt persistence interface.

PostgreSQL is authoritative (CLAUDE.md "Data authority": "assessment
attempts/current learning status -> Learning service in PostgreSQL").
Append-oriented only (CLAUDE.md "Tutor learning rules": "Assessment
history is append-oriented evidence. Do not overwrite history with a
single 'mastery score.'") — there is deliberately no update or delete
method. A concrete PostgreSQL-backed implementation is added alongside
the assessment_attempts migration, not here.
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable
from uuid import UUID

from netra_api.learning.assessment.models import AssessmentAttempt
from netra_api.platform.auth_context import AuthContext


class AssessmentHistoryRepository(Protocol):
    """Typed contract for appending and reading assessment history."""

    def append(self, auth: AuthContext, attempt: AssessmentAttempt) -> AssessmentAttempt:
        """Durably record one attempt.

        Idempotent by attempt_id: appending an already-known attempt_id
        returns the existing record rather than duplicating it (CLAUDE.md
        "Background jobs"/"Session rules" idempotency principle applied
        here to assessment writes as well).
        """
        ...

    def list_for_concept(self, auth: AuthContext, concept_id: str) -> list[AssessmentAttempt]:
        ...

    def list_for_account(self, auth: AuthContext) -> list[AssessmentAttempt]:
        ...

    def get(self, auth: AuthContext, attempt_id: UUID) -> Optional[AssessmentAttempt]:
        """Return one attempt, or None when no such attempt exists.

        Raise netra_api.platform.errors.AuthorizationError if attempt_id
        exists but does not belong to auth.account_id.

        "Absent" and "not yours" are deliberately different outcomes here,
        and neither may be collapsed into the other. Returning None for an
        unknown id is what lets a caller ask "has this turn already been
        committed?" without treating a genuine authorization failure as a
        cache miss — see
        netra_api.learning.assessment.service.LearningService.find_committed_attempt,
        which relies on exactly that distinction to make a retransmitted
        submission replay instead of fail.
        """
        ...


@runtime_checkable
class AtomicAnswerCommitter(Protocol):
    """Durable stores commit an answer as ONE transaction (INT-08).

    ``commit_answer`` must, atomically: close the still-pending question at
    exactly ``attempt.question_version`` for ``auth``'s account (raising
    QuestionNotPendingError when it is absent, another account's, already
    answered or at another version), append the attempt, and write the
    projection outbox event. Replaying an already-committed ``attempt_id``
    returns the stored attempt and writes nothing. A failure leaves none of
    the three effects. LearningService uses this whenever its repository
    provides it; repositories without it are non-durable fixtures.
    """

    async def commit_answer(self, auth: AuthContext, attempt: AssessmentAttempt) -> AssessmentAttempt:
        ...
