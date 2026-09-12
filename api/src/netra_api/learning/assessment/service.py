"""Learning service: validates and commits authoritative assessment changes.

CLAUDE.md "Tutor learning rules": "Tutor output may PROPOSE a learning
event. The Learning service validates and commits authoritative
assessment changes." This is the only path an AssessmentAttempt is ever
written through — the Tutor calls propose_event as a bounded tool call
(see netra_api.coordinator.tool_registry), never a direct mutation
(CLAUDE.md "The Tutor must not ... directly set mastery").

derive_status_from_history is pure, deterministic local computation
(like netra_worker.runtime.retries's backoff formula), so it is
implemented in full rather than stubbed; propose_event's actual
PostgreSQL commit is not, pending a concrete
AssessmentHistoryRepository implementation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from netra_api.learning.assessment.models import (
    AssessmentAttempt,
    AttemptOutcome,
    CurrentLearningStatus,
    LearningEventProposal,
    LearningStatus,
)
from netra_api.learning.assessment.repository import AssessmentHistoryRepository
from netra_api.platform.auth_context import AuthContext
from netra_api.platform.errors import NetraError


class StatusDerivationPolicy(BaseModel):
    """The thresholds that turn assessment history into a status label.

    Every field is required and has no default, deliberately.

    learning.md: "Current learning status is derived using an explicit
    versioned application policy" and "If derivation/scheduling policy is
    unspecified, report it and leave an explicit stub. Do not turn
    illustrative handbook intervals or thresholds into hidden product
    policy."

    Earlier scaffold work carried a 7-day window and a 2-correct
    requirement as module constants. Those were illustrative values, not
    an approved product decision, and as module constants they were
    indistinguishable from one. Requiring them to be supplied means the
    numbers now live wherever a human configured them, and the
    application cannot run until someone does.

    policy_version identifies which approved revision produced a derived
    status, so a label can be re-derived and audited later.
    """

    model_config = ConfigDict(frozen=True)

    policy_version: str = Field(min_length=1)
    recent_window: timedelta
    """How far back an attempt still counts toward "demonstrated recently"."""
    recent_correct_required: int = Field(ge=1)
    """How many correct attempts inside recent_window are required before a
    concept counts as demonstrated."""


class InvalidLearningEventProposalError(NetraError):
    """Raised when a LearningEventProposal is missing fields its event_type requires."""


def _validate_proposal(proposal: LearningEventProposal) -> None:
    if proposal.event_type == "answer_evaluated" and (
        proposal.answer is None or proposal.outcome is None or proposal.question_id is None
    ):
        raise InvalidLearningEventProposalError(
            "answer_evaluated proposals require question_id, answer, and outcome"
        )
    if proposal.event_type == "hint_used" and proposal.hints_used < 1:
        raise InvalidLearningEventProposalError("hint_used proposals require hints_used >= 1")


def derive_status_from_history(
    attempts: list[AssessmentAttempt],
    policy: StatusDerivationPolicy,
    now: Optional[datetime] = None,
) -> LearningStatus:
    """Recompute a LearningStatus label from append-only history.

    Never trusts a cached score (learning.md "Do not invent
    probabilities, mastery scores, confidence thresholds or new labels")
    — this always walks the full history it is given, and produces one of
    the four permitted labels, never a number.

    The shape of the rule is: the most recent attempt must be correct and
    enough recent correct attempts must exist to count as demonstrated; a
    most recent incorrect or partial attempt means the concept needs
    review; anything else is still developing. How recent and how many
    come from policy, which a human must supply.
    """

    if not attempts:
        return LearningStatus.NOT_ASSESSED

    reference_now = now or datetime.now(timezone.utc)
    ordered = sorted(attempts, key=lambda attempt: attempt.created_at)
    latest = ordered[-1]
    recent_correct = [
        attempt
        for attempt in ordered
        if attempt.outcome == AttemptOutcome.CORRECT
        and reference_now - attempt.created_at <= policy.recent_window
    ]

    if (
        latest.outcome == AttemptOutcome.CORRECT
        and len(recent_correct) >= policy.recent_correct_required
    ):
        return LearningStatus.DEMONSTRATED_RECENTLY
    if latest.outcome in (AttemptOutcome.INCORRECT, AttemptOutcome.PARTIAL):
        return LearningStatus.NEEDS_REVIEW
    return LearningStatus.DEVELOPING


class LearningService:
    """Application-facing learning operations. No SQL and no LLM calls here."""

    def __init__(
        self,
        repository: AssessmentHistoryRepository,
        status_derivation_policy: StatusDerivationPolicy,
    ) -> None:
        self._repository = repository
        self._status_derivation_policy = status_derivation_policy

    def propose_event(self, auth: AuthContext, proposal: LearningEventProposal) -> AssessmentAttempt:
        """Validate a Tutor-proposed learning event and commit it as an AssessmentAttempt.

        TODO: build the AssessmentAttempt and call
        self._repository.append(...) once a concrete
        AssessmentHistoryRepository exists. The checks below (account
        ownership, proposal shape) are the boundary every commit path
        must pass through first.
        """

        auth.assert_owns_account(proposal.account_id)
        _validate_proposal(proposal)
        raise NotImplementedError("learning event commit is not yet implemented")

    def derive_current_status(self, auth: AuthContext, concept_id: str) -> CurrentLearningStatus:
        """Read history for concept_id and derive its current LearningStatus."""

        attempts = self._repository.list_for_concept(auth, concept_id)
        status = derive_status_from_history(attempts, self._status_derivation_policy)
        last_assessed_at = max((attempt.created_at for attempt in attempts), default=None)
        return CurrentLearningStatus(
            concept_id=concept_id,
            status=status,
            based_on_attempts=len(attempts),
            last_assessed_at=last_assessed_at,
        )
