"""Pending-question persistence interface.

CLAUDE.md "Tutor learning rules": "Persist a pending question before
delivering it to the student." This is the boundary the Tutor's
question-delivery path must call through: an ApprovedQuestion is
persisted here first; only after that succeeds may
netra_api.learning.quiz.models.StudentFacingQuestion be delivered over
the wire and netra_api.session.state.PendingQuestionRef be recorded on
the session (see netra_api.session.repository). No SQL is implemented
here — this only fixes the interface. A concrete PostgreSQL-backed
implementation is added alongside the pending_questions migration, not
here.
"""

from __future__ import annotations

from typing import Optional, Protocol

from netra_api.learning.quiz.models import ApprovedQuestion
from netra_api.platform.auth_context import AuthContext


class PendingQuestionRepository(Protocol):
    """Typed contract for persisting a question before it is delivered."""

    def persist_pending(self, auth: AuthContext, question: ApprovedQuestion) -> ApprovedQuestion:
        """Durably store question so it can be resolved even if the
        response carrying it never reaches the client. Must be called
        and succeed before the question is sent to the student."""
        ...

    def get_pending(self, auth: AuthContext, question_id: str) -> Optional[ApprovedQuestion]:
        ...

    def mark_answered(self, auth: AuthContext, question_id: str, question_version: int) -> None:
        """Clear the pending flag once the student's answer has been
        finalized and graded. Does not itself write an AssessmentAttempt
        — see netra_api.learning.assessment.service.LearningService."""
        ...
