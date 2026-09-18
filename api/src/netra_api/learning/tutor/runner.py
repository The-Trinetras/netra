"""TutorRunner: the adapter M1's composition calls to run one Tutor handoff.

INT-09 (docs/team/integration-playbook.md). M1 owns the Coordinator,
session state and API composition; M4 owns the Tutor loop. This module is
the seam between them, so M1 never has to reason about Tutor internals
and the Tutor never touches session state:

- It builds the Tutor's state from the typed handoff on the SAME
  TurnBudget instance the originating Coordinator turn is spending
  (build_turn_state rejects a budget that outlives the handoff deadline).
  Model decisions, tool calls, deadline and cancellation are therefore
  shared, never reset.
- It separates proposed learning events into those ALREADY COMMITTED by
  the Learning service (they carry ``attempt_id``) and those that are
  reported on the wire only. **An event with an attempt_id is already
  committed. M1 must never propose or commit it again**; doing so would
  either replay (same request) or raise QuestionNotPendingError (new
  request), never double-commit, but it is still a contract violation.
- It describes, as a typed value, what the Tutor's result means for the
  session's pending question. The Session service owns and applies that
  state (incrementing hints_used, clearing an answered question); this
  adapter only reports it. The transition is a PROPOSED M1/M4 contract,
  not an approved one — see docs/team/handoffs/M4.md.
- It verifies that a newly delivered question is actually persisted
  before M1 may deliver it (learning.md: "Persist the pending question
  before delivering it to the client"). If the pending-question store
  cannot return it, the runner raises instead of letting an unresolvable
  question reach the student.
- It turns the deliberately unimplemented optional-check grounding
  decision (NotImplementedError from the production validator) into
  TutorCapabilityPendingError, a NetraError M1 can map to a safe client
  error, while keeping it distinguishable from an ordinary failed turn.
- It flags a turn whose budget was cancelled while it ran. A cancelled or
  superseded generation must not be delivered or resume audio
  (message-flow.md); M1 decides what to do with the flag, the runner
  never suppresses output on its own.

No persistence, transport, tracing or provider construction happens here.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from netra_api.coordinator.handoff import (
    CoordinatorToTutorHandoff,
    ProposedLearningEvent,
    TutorToCoordinatorResult,
)
from netra_api.coordinator.limits import TurnBudget
from netra_api.learning.tutor.agent import TutorServices, build_turn_state, run_turn
from netra_api.platform.auth_context import AuthContext
from netra_api.platform.awaitables import maybe_await
from netra_api.platform.errors import NetraError


class TutorCapabilityPendingError(NetraError):
    """The requested Tutor capability is blocked on an unmade decision.

    Today this is only optional-check grounding (D2). Not a provider
    outage and not an ordinary failed turn: M1 should report the check as
    unavailable, not retry it.
    """


class UndeliverableQuestionError(NetraError):
    """The Tutor returned a new pending question the store cannot resolve.

    Raised instead of delivering it: a question the student was asked but
    the server cannot later resolve is exactly what persist-before-deliver
    prevents.
    """


class PendingQuestionChange(str, Enum):
    """What a Tutor result means for SessionState.pending_question.

    PROPOSED M1/M4 contract. The Session service owns the state and
    decides how to apply it; these values only name the Tutor's outcome.
    """

    NONE = "none"
    """No question was pending and none was created."""
    UNCHANGED = "unchanged"
    """The pending question stays exactly as it was: a clarification, a
    refused or failed turn, or a turn that needed more evidence."""
    HINT_GIVEN = "hint_given"
    """The pending question stays; one hint was delivered for it, so
    hints_used should become the handoff's value plus one."""
    NEW_QUESTION = "new_question"
    """A newly persisted question is now pending, with no hints used."""
    ANSWERED = "answered"
    """The pending question received a committed attempt and is no longer
    pending. Also reported for a replay of that commit."""


@dataclass(frozen=True)
class PendingQuestionOutcome:
    change: PendingQuestionChange
    question_id: Optional[str] = None
    question_version: Optional[int] = None
    hints_used: Optional[int] = None
    """For UNCHANGED/HINT_GIVEN/NEW_QUESTION, the hints_used value the
    session should hold afterwards. None for NONE/ANSWERED."""


@dataclass(frozen=True)
class TutorTurnOutcome:
    result: TutorToCoordinatorResult
    committed_events: tuple[ProposedLearningEvent, ...]
    """Events the Learning service already committed (attempt_id set).
    Report them; never commit them again."""
    uncommitted_events: tuple[ProposedLearningEvent, ...]
    """Events reported on the wire only (concept_exposed, hint_used). No
    durable record exists for these until the D3 factual-history record is
    approved and implemented; they are not evidence of delivery or play."""
    pending_question: PendingQuestionOutcome
    cancelled_during_turn: bool
    """The shared budget was cancelled before the turn returned. Its
    output belongs to a cancelled/superseded generation."""


class TutorRunner:
    """Run one Tutor handoff for M1's composition. One instance per process."""

    def __init__(self, services: TutorServices) -> None:
        self._services = services

    async def run(
        self,
        handoff: CoordinatorToTutorHandoff,
        auth: AuthContext,
        budget: TurnBudget,
    ) -> TutorTurnOutcome:
        """Run ``handoff`` on the originating turn's ``budget``.

        ``auth`` must be the originating turn's authenticated context (its
        request_id is what makes attempt commits replay-safe). ``budget``
        must be the originating TurnBudget instance, not a copy.

        Raises AuthorizationError unchanged, TurnBudgetExceededError if the
        budget outlives the handoff deadline, TutorCapabilityPendingError
        for the blocked check path and UndeliverableQuestionError if a new
        question cannot be resolved from the store.
        """

        auth.assert_owns_session(handoff.session_id)
        state = build_turn_state(handoff, auth, budget)
        try:
            result = await run_turn(state, self._services)
        except NotImplementedError as pending:
            raise TutorCapabilityPendingError(
                f"Tutor {handoff.mode} is blocked on an unimplemented decision"
            ) from pending

        committed = tuple(e for e in result.proposed_learning_events if e.attempt_id is not None)
        uncommitted = tuple(e for e in result.proposed_learning_events if e.attempt_id is None)
        return TutorTurnOutcome(
            result=result,
            committed_events=committed,
            uncommitted_events=uncommitted,
            pending_question=await self._pending_question_outcome(handoff, auth, result, committed),
            cancelled_during_turn=budget.cancelled,
        )

    async def _pending_question_outcome(
        self,
        handoff: CoordinatorToTutorHandoff,
        auth: AuthContext,
        result: TutorToCoordinatorResult,
        committed: tuple[ProposedLearningEvent, ...],
    ) -> PendingQuestionOutcome:
        prior = handoff.pending_question

        if prior is not None and any(e.event_type == "answer_evaluated" for e in committed):
            return PendingQuestionOutcome(change=PendingQuestionChange.ANSWERED)

        returned_id = result.pending_question_id
        if returned_id is None or result.status != "awaiting_student_answer":
            if prior is None:
                return PendingQuestionOutcome(change=PendingQuestionChange.NONE)
            return _unchanged(prior.question_id, prior.question_version, prior.hints_used)

        if prior is not None and returned_id == prior.question_id:
            if any(e.event_type == "hint_used" for e in result.proposed_learning_events):
                return PendingQuestionOutcome(
                    change=PendingQuestionChange.HINT_GIVEN,
                    question_id=prior.question_id,
                    question_version=prior.question_version,
                    hints_used=prior.hints_used + 1,
                )
            return _unchanged(prior.question_id, prior.question_version, prior.hints_used)

        # A question the handoff did not already reference: it must be
        # resolvable from the store before anyone may deliver it.
        persisted = await maybe_await(self._services.pending_questions.get_pending(auth, returned_id))
        if persisted is None:
            raise UndeliverableQuestionError(
                "the Tutor returned a pending question that is not persisted for this account"
            )
        return PendingQuestionOutcome(
            change=PendingQuestionChange.NEW_QUESTION,
            question_id=persisted.question_id,
            question_version=persisted.question_version,
            hints_used=0,
        )


def _unchanged(question_id: str, question_version: int, hints_used: int) -> PendingQuestionOutcome:
    return PendingQuestionOutcome(
        change=PendingQuestionChange.UNCHANGED,
        question_id=question_id,
        question_version=question_version,
        hints_used=hints_used,
    )
