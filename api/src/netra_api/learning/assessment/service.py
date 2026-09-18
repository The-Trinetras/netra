"""Learning service: validates and commits authoritative assessment changes.

CLAUDE.md "Tutor learning rules": "Tutor output may PROPOSE a learning
event. The Learning service validates and commits authoritative
assessment changes." This is the only path an AssessmentAttempt is ever
written through — the Tutor calls propose_event as a bounded tool call
(see netra_api.coordinator.tool_registry), never a direct mutation
(CLAUDE.md "The Tutor must not ... directly set mastery").

derive_status_from_history is pure, deterministic local computation
(like netra_worker.runtime.retries's backoff formula), so it is
implemented in full rather than stubbed. propose_event commits through
AtomicAnswerCommitter whenever the repository provides it — the durable
netra_api.learning.postgres.PostgresLearningStore closes the pending
question, appends the attempt and writes the projection outbox event in
one PostgreSQL transaction (INT-08). Repositories without it are
non-durable in-memory fixtures; their sequential append/mark_answered path
is neither atomic nor projected and is never registered in production.

Methods are async because the durable store is async; they accept
synchronous fixture repositories through maybe_await.

propose_event only ever commits event_type == "answer_evaluated": that
is the only LearningEventType AssessmentAttempt (question_id/
question_version/answer/outcome all required, non-optional) can
represent. concept_exposed/hint_used/review_requested describe activity
AssessmentAttempt has no fields for — learning.md: "Record delivered
study activity, each answer, student-stated reasoning, feedback and
assistance separately" describes a richer record than this model
carries (see docs/team/M4.md "Current source evidence"). Rather than
force those event types into an answer-shaped row (fabricating a
question_id/answer that was never asked), propose_event fails closed
with UnrepresentableLearningEventError — see docs/team/handoffs/M4.md
for the schema-gap proposal this blocks on (M1/M2/M5 review, CLAUDE.md
"Prepare the minimum factual-history schema/handoff proposal").
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field

from netra_api.learning.assessment.models import (
    AssessmentAttempt,
    AttemptOutcome,
    CurrentLearningStatus,
    LearningEventProposal,
    LearningStatus,
)
from netra_api.learning.assessment.repository import AssessmentHistoryRepository, AtomicAnswerCommitter
from netra_api.learning.quiz.repository import PendingQuestionRepository
from netra_api.platform.auth_context import AuthContext
from netra_api.platform.awaitables import maybe_await
from netra_api.platform.errors import NetraError, ResourceUnavailableError


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


class UnrepresentableLearningEventError(NetraError):
    """Raised for an event_type AssessmentAttempt has no fields to record.

    Fails closed rather than fabricating a question_id/answer that was
    never asked (CLAUDE.md: "Unimplemented ... persistence must fail
    closed, never return success"). See the module docstring and
    docs/team/handoffs/M4.md for the schema-gap proposal this blocks on.
    """

    def __init__(self, event_type: str) -> None:
        self.event_type = event_type
        super().__init__(
            f"event_type {event_type!r} cannot be committed as an AssessmentAttempt yet "
            "(question_id/question_version/answer/outcome are required fields with no "
            "representation for non-answer activity); see docs/team/handoffs/M4.md"
        )


class QuestionNotPendingError(NetraError):
    """Raised when an answer_evaluated proposal targets a question that is
    not (or no longer) pending for this account.

    Covers both "never persisted"/"belongs to someone else" (get_pending
    returns None) and "already answered" (mark_answered already cleared
    it) — learning.md "finality" check: only a still-pending question may
    be finalized, so a duplicate grade of the same question is rejected
    here rather than silently re-graded.
    """

    def __init__(self, question_id: str) -> None:
        self.question_id = question_id
        super().__init__(f"question_id {question_id!r} is not currently pending for this account")


class QuestionVersionMismatchError(NetraError):
    """Raised when a proposal's question_version does not match the
    persisted pending question's version — stale client state must not
    be graded against a version the student was not actually shown."""

    def __init__(self, question_id: str, expected: int, actual: int) -> None:
        self.question_id = question_id
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"question_id {question_id!r} is pending at version {expected}, "
            f"but the proposal targets version {actual}"
        )


def _validate_proposal(proposal: LearningEventProposal) -> None:
    if proposal.event_type == "answer_evaluated" and (
        proposal.answer is None
        or proposal.outcome is None
        or proposal.question_id is None
        or proposal.question_version is None
        or proposal.evaluated_by is None
    ):
        raise InvalidLearningEventProposalError(
            "answer_evaluated proposals require question_id, question_version, "
            "answer, outcome, and evaluated_by"
        )
    if proposal.event_type == "hint_used" and proposal.hints_used < 1:
        raise InvalidLearningEventProposalError("hint_used proposals require hints_used >= 1")


_ATTEMPT_ID_NAMESPACE = UUID("2f1a8a3e-8c1a-4c7a-9c3d-9d9f6b4b7a10")
"""Fixed namespace for deriving replay-safe AssessmentAttempt IDs.

Not a secret and not a schema value — an arbitrary, fixed constant so
uuid5 derivation is reproducible from source alone (any value works;
this one only needs to never change once committed, or previously
derived attempt_ids would stop matching on replay).
"""


def derive_attempt_id(request_id: UUID, question_id: str, question_version: int) -> UUID:
    """Deterministic, replay-safe attempt_id for one answer_evaluated commit.

    message-flow.md: "request_id is minted once per logical user action
    ... and reused verbatim if that same action must be retransmitted."
    AuthContext.request_id is already that stable per-turn identifier
    (platform/auth_context.py), supplied by the application layer, never
    the model — so deriving attempt_id from it means a retried turn.submit
    (same request_id, same question) reproduces the exact same attempt_id
    without any new wire field or LearningEventProposal change.
    AssessmentHistoryRepository.append is documented idempotent by
    attempt_id: "appending an already-known attempt_id returns the
    existing record rather than duplicating it" — this is what makes that
    idempotency actually reachable for a duplicate submission, rather
    than only for a caller that already knows an ID to retry with.
    """

    return uuid5(_ATTEMPT_ID_NAMESPACE, f"{request_id}:{question_id}:{question_version}")


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
        status_derivation_policy: Optional[StatusDerivationPolicy],
        pending_question_repository: PendingQuestionRepository,
    ) -> None:
        """status_derivation_policy is legacy: automatic learning labels are a
        removed requirement and no approved thresholds exist, so production
        passes None and derive_current_status then refuses. Committing and
        reading factual history never needs it."""
        self._repository = repository
        self._status_derivation_policy = status_derivation_policy
        self._pending_question_repository = pending_question_repository

    async def propose_event(self, auth: AuthContext, proposal: LearningEventProposal) -> AssessmentAttempt:
        """Validate a Tutor-proposed learning event and commit it as an AssessmentAttempt.

        Only event_type == "answer_evaluated" can be committed today — see
        the module docstring for why the other LearningEventType values
        fail closed with UnrepresentableLearningEventError instead.

        Validation order mirrors learning.md "Learning service validates
        ownership, question identity, evidence, rubric, response finality
        and replay identity before committing":
        1. account ownership (auth.assert_owns_account)
        2. proposal shape (_validate_proposal)
        3. representability (event_type)
        4. replay: a submission this same turn already committed returns
           that attempt unchanged (see find_committed_attempt), so a
           duplicate delivery reuses its effect rather than appending a
           second attempt or failing the finality check below.
        5. question identity + finality: the target question must still
           be pending for this account (QuestionNotPendingError otherwise
           — covers both "never asked"/"not yours" and "already answered")
        6. version: the proposal must target the version actually shown
           (QuestionVersionMismatchError otherwise)

        Evidence/rubric grounding is not checked here: ApprovedQuestion
        carries no evidence_refs today (see
        netra_api.learning.quiz.validator.validate_draft_is_grounded,
        deliberately unimplemented pending the same evidence-grounding
        product decision) — a gap, not something this method can enforce
        against a model that does not carry the data.
        """

        auth.assert_owns_account(proposal.account_id)
        _validate_proposal(proposal)

        if proposal.event_type != "answer_evaluated":
            raise UnrepresentableLearningEventError(proposal.event_type)

        assert proposal.question_id is not None
        assert proposal.question_version is not None
        assert proposal.answer is not None
        assert proposal.outcome is not None
        assert proposal.evaluated_by is not None

        # Replay before finality. A retransmitted turn has already cleared
        # its own pending question via mark_answered below, so checking
        # "still pending" first would reject the very submission that
        # succeeded. Returning the original attempt is what makes a
        # duplicate delivery reuse its effect instead of erroring, and it
        # holds here at the authoritative layer rather than depending on
        # every caller to check first.
        replayed = await self.find_committed_attempt(
            auth, proposal.question_id, proposal.question_version
        )
        if replayed is not None:
            return replayed

        pending = await maybe_await(self._pending_question_repository.get_pending(auth, proposal.question_id))
        if pending is None:
            raise QuestionNotPendingError(proposal.question_id)
        if pending.question_version != proposal.question_version:
            raise QuestionVersionMismatchError(
                proposal.question_id, pending.question_version, proposal.question_version
            )

        attempt = AssessmentAttempt(
            attempt_id=derive_attempt_id(auth.request_id, proposal.question_id, proposal.question_version),
            account_id=proposal.account_id,
            concept_id=proposal.concept_id,
            question_id=proposal.question_id,
            question_version=proposal.question_version,
            answer=proposal.answer,
            outcome=proposal.outcome,
            hints_used=proposal.hints_used,
            evaluated_by=proposal.evaluated_by,
            created_at=datetime.now(timezone.utc),
        )
        if isinstance(self._repository, AtomicAnswerCommitter):
            # Durable path: question closure, attempt and projection outbox
            # event commit together, and a concurrent second answer to the
            # same question is refused inside that transaction.
            return await self._repository.commit_answer(auth, attempt)
        # Non-durable fixture repositories only (see the module docstring).
        committed = await maybe_await(self._repository.append(auth, attempt))
        await maybe_await(
            self._pending_question_repository.mark_answered(
                auth, proposal.question_id, proposal.question_version
            )
        )
        return committed

    async def find_committed_attempt(
        self, auth: AuthContext, question_id: str, question_version: int
    ) -> Optional[AssessmentAttempt]:
        """Return the attempt this exact turn already committed, if any.

        The replay counterpart of propose_event. Because attempt_id is
        derived from auth.request_id (see derive_attempt_id), a
        retransmitted turn.submit asks for the same id the first
        transmission committed. A caller can therefore tell a genuine
        retransmission apart from a new attempt *before* doing any work,
        rather than discovering it at the commit.

        This is what stops a retransmission from looking like an error:
        propose_event clears the pending question via mark_answered, so a
        replayed turn would otherwise find nothing pending and fail, even
        though its attempt was committed successfully the first time.

        Returns None when this turn has committed nothing yet. A different
        request_id is a different turn and therefore a genuinely separate
        attempt, never a replay of this one.
        """

        return await maybe_await(
            self._repository.get(auth, derive_attempt_id(auth.request_id, question_id, question_version))
        )

    async def list_attempts_for_concepts(
        self, auth: AuthContext, concept_ids: Sequence[str]
    ) -> list[AssessmentAttempt]:
        """Factual attempt history for the given concepts, newest last.

        Returns the committed records themselves, never a derived label:
        automatic mastery labelling is a removed requirement, so callers
        that want to adapt teaching read what the student actually
        answered rather than a status computed from it.

        Raises whatever the repository raises when history is unavailable.
        Callers must not treat that failure as "no history" — learning.md:
        "An unavailable history service is not evidence of no history."
        """

        attempts: list[AssessmentAttempt] = []
        for concept_id in concept_ids:
            attempts.extend(await maybe_await(self._repository.list_for_concept(auth, concept_id)))
        return sorted(attempts, key=lambda attempt: attempt.created_at)

    async def derive_current_status(self, auth: AuthContext, concept_id: str) -> CurrentLearningStatus:
        """Read history for concept_id and derive its legacy LearningStatus.

        Legacy compatibility only (automatic labels are a removed
        requirement). Refuses when no policy was configured rather than
        inventing thresholds.
        """

        if self._status_derivation_policy is None:
            raise ResourceUnavailableError("legacy learning-status derivation is not configured")
        attempts = await maybe_await(self._repository.list_for_concept(auth, concept_id))
        status = derive_status_from_history(attempts, self._status_derivation_policy)
        last_assessed_at = max((attempt.created_at for attempt in attempts), default=None)
        return CurrentLearningStatus(
            concept_id=concept_id,
            status=status,
            based_on_attempts=len(attempts),
            last_assessed_at=last_assessed_at,
        )
