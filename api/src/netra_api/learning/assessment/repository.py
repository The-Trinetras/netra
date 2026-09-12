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

from typing import Protocol
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

    def get(self, auth: AuthContext, attempt_id: UUID) -> AssessmentAttempt:
        """Raise netra_api.platform.errors.AuthorizationError if attempt_id
        does not belong to auth.account_id."""
        ...
