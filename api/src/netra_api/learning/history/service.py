"""Validation and commit path for factual activity records (D3) — PROPOSED.

See netra_api.learning.history.models for the record design and why it is
a proposal. Like LearningService, this validates before committing and
never trusts caller-supplied identity: account, session and request come
from the AuthContext, and references to questions and attempts are checked
against their own authoritative repositories.

No concrete repository exists (it needs M2's reviewed migration), so no
production path can report a record as committed. Not yet called by the
Tutor or the runner.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Protocol, Sequence
from uuid import UUID

from netra_api.learning.assessment.repository import AssessmentHistoryRepository
from netra_api.learning.history.models import (
    ActivityKind,
    ActivityProposal,
    ActivityRecord,
    derive_activity_id,
)
from netra_api.learning.quiz.repository import PendingQuestionRepository
from netra_api.platform.auth_context import AuthContext
from netra_api.platform.errors import NetraError


class ActivityHistoryRepository(Protocol):
    """Append-only store for ActivityRecord. No update or delete method."""

    def append(self, auth: AuthContext, record: ActivityRecord) -> ActivityRecord:
        """Idempotent by activity_id: an already-known id returns the stored record."""
        ...

    def get(self, auth: AuthContext, activity_id: UUID) -> Optional[ActivityRecord]:
        """None when absent; raise AuthorizationError when it belongs to another account."""
        ...

    def list_for_concepts(self, auth: AuthContext, concept_ids: Sequence[str]) -> list[ActivityRecord]:
        ...


class ActivityReferenceError(NetraError):
    """The question or attempt an activity refers to is not valid for this account."""


class ActivityReplayConflictError(NetraError):
    """The same request/kind/ordinal was already recorded with different content.

    A genuine retransmission reproduces identical content; different
    content under the same identity is refused rather than silently
    replacing or duplicating history.
    """


class ActivityHistoryService:
    def __init__(
        self,
        repository: ActivityHistoryRepository,
        attempts: AssessmentHistoryRepository,
        pending_questions: PendingQuestionRepository,
    ) -> None:
        self._repository = repository
        self._attempts = attempts
        self._pending_questions = pending_questions

    def record(self, auth: AuthContext, proposal: ActivityProposal) -> ActivityRecord:
        activity_id = derive_activity_id(auth.request_id, proposal.kind, proposal.ordinal)

        existing = self._repository.get(auth, activity_id)
        if existing is not None:
            if not _same_content(existing, proposal):
                raise ActivityReplayConflictError(
                    f"activity {activity_id} was already recorded with different content"
                )
            return existing

        self._check_references(auth, proposal)

        record = ActivityRecord(
            activity_id=activity_id,
            account_id=auth.account_id,
            session_id=auth.session_id,
            request_id=auth.request_id,
            lesson_id=proposal.lesson_id,
            kind=proposal.kind,
            concept_ids=tuple(proposal.concept_ids),
            text=proposal.text,
            evidence_ids=tuple(proposal.evidence_ids),
            question_id=proposal.question_id,
            question_version=proposal.question_version,
            attempt_id=proposal.attempt_id,
            original_text=proposal.original_text,
            recorded_at=datetime.now(timezone.utc),
        )
        return self._repository.append(auth, record)

    def list_for_concepts(self, auth: AuthContext, concept_ids: Sequence[str]) -> list[ActivityRecord]:
        """Factual records, oldest first. Raises when unavailable: an
        unavailable history service is not evidence of no history."""

        records = self._repository.list_for_concepts(auth, list(concept_ids))
        return sorted(records, key=lambda record: record.recorded_at)

    def _check_references(self, auth: AuthContext, proposal: ActivityProposal) -> None:
        if proposal.kind is ActivityKind.HINT_GENERATED:
            assert proposal.question_id is not None and proposal.question_version is not None
            pending = self._pending_questions.get_pending(auth, proposal.question_id)
            if pending is None or pending.question_version != proposal.question_version:
                raise ActivityReferenceError("a hint must belong to a question pending for this account")

        if proposal.attempt_id is not None:
            # AuthorizationError from another account's attempt propagates.
            attempt = self._attempts.get(auth, proposal.attempt_id)
            if attempt is None:
                raise ActivityReferenceError("the referenced attempt is not committed")
            if proposal.question_id is not None and (
                attempt.question_id != proposal.question_id
                or attempt.question_version != proposal.question_version
            ):
                raise ActivityReferenceError("the attempt belongs to a different question")


def _same_content(existing: ActivityRecord, proposal: ActivityProposal) -> bool:
    return (
        existing.kind is proposal.kind
        and existing.lesson_id == proposal.lesson_id
        and existing.text == proposal.text
        and existing.concept_ids == tuple(proposal.concept_ids)
        and existing.evidence_ids == tuple(proposal.evidence_ids)
        and existing.question_id == proposal.question_id
        and existing.question_version == proposal.question_version
        and existing.attempt_id == proposal.attempt_id
        and existing.original_text == proposal.original_text
    )
