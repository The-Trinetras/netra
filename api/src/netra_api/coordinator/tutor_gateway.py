"""Typed Coordinator -> Tutor delegation and result validation.

Uses only shared/contracts/agent/v1 via coordinator/handoff.py. The Tutor
receives references to validated evidence, the original utterance, bounded
student/tutor dialogue and the existing pending question — never the
Coordinator's prompt, ledger reasoning, credentials or whole history.

Budget: the Tutor runs on the SAME TurnBudget instance as the originating
turn (learning.tutor.agent.build_turn_state rejects a later deadline). The
handoff's deadline_at alone would not carry spent counters, so the instance
is passed in-process.

Result validation before anything reaches the student:
- contract shape (strict model) and matching handoff_id;
- every evidence id the Tutor cites was handed to it;
- ``awaiting_student_answer`` names a question the Learning repository has
  already persisted (persist before delivery), and no public segment repeats
  that question's private correct answer;
- proposed learning events are forwarded to the Learning service boundary when
  one is registered; ``review_requested`` is a legacy compatibility value and
  is recorded, not acted on. Coordinator never commits learning records.

``assessment_summaries`` is required by the v1 contract but its legacy status
vocabulary is outside current scope. It is sent empty unless M4 registers a
provider; this is structurally valid and is NOT a claim that no history exists
(factual-history migration pending with M4).
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any, Optional, Protocol
from uuid import UUID, uuid4

from pydantic import ValidationError

from netra_api.coordinator.decisions import TutorRequest
from netra_api.coordinator.evidence_check import EvidenceLedger
from netra_api.coordinator.handoff import (
    AssessmentSummary,
    CoordinatorToTutorHandoff,
    DialogueTurn,
    EvidenceRef,
    PendingQuestion,
    TutorToCoordinatorResult,
)
from netra_api.coordinator.limits import TurnBudget
from netra_api.learning.quiz.models import QuestionKind
from netra_api.learning.tutor.agent import build_turn_state
from netra_api.learning.tutor.state import TutorTurnState
from netra_api.platform.auth_context import AuthContext
from netra_api.platform.awaitables import maybe_await
from netra_api.platform.errors import NetraError, ResourceUnavailableError, TurnCancelledError
from netra_api.platform.observability import TurnTrace
from netra_api.session.dialogue import DialogueLog
from netra_api.session.navigation import public_question_plan
from netra_api.session.outputs import PlannedQuestion
from netra_api.session.state import SessionState


class TutorRunner(Protocol):
    """M4-provided bounded Tutor execution: one state in, one result out."""

    async def run(self, state: TutorTurnState) -> TutorToCoordinatorResult:
        ...


class LearningTutorRunner:
    """Adapter from M4's merged ``run_turn(state, services)`` to TutorRunner.

    ``state`` is built by M4's own ``build_turn_state`` inside the gateway,
    carrying the ORIGINATING TurnBudget instance, so every Tutor model
    decision and evidence resolution spends the Coordinator's counters and
    deadline, and cancelling the turn is visible to the Tutor.

    M4 commits answered attempts itself through LearningService.propose_event
    and reports them as events carrying ``attempt_id``; this adapter and the
    gateway never commit or re-propose anything.
    """

    def __init__(self, services: Any) -> None:
        self.services = services

    @property
    def pending_questions(self) -> Any:
        return self.services.pending_questions

    async def run(self, state: TutorTurnState) -> TutorToCoordinatorResult:
        from netra_api.learning.tutor.agent import run_turn

        return await run_turn(state, self.services)


class AssessmentSummaryProvider(Protocol):
    async def summaries(self, auth: AuthContext, concept_ids: tuple[str, ...]) -> list[AssessmentSummary]:
        ...


class LearningProposalSink(Protocol):
    async def forward(self, auth: AuthContext, handoff_id: UUID, result: TutorToCoordinatorResult) -> None:
        ...


class HandoffRejectedError(NetraError):
    """The Tutor result failed validation; ``check`` is a safe code."""

    def __init__(self, check: str) -> None:
        self.check = check
        super().__init__(check)


@dataclass(frozen=True)
class TutorDelegation:
    handoff: CoordinatorToTutorHandoff
    result: TutorToCoordinatorResult
    question: Optional[PlannedQuestion]


class TutorGateway:
    def __init__(
        self,
        runner: TutorRunner,
        *,
        pending_questions: Any,
        dialogue: Optional[DialogueLog] = None,
        summaries: Optional[AssessmentSummaryProvider] = None,
        learning_sink: Optional[LearningProposalSink] = None,
    ) -> None:
        self._runner = runner
        self._pending_questions = pending_questions
        self._dialogue = dialogue
        self._summaries = summaries
        self._learning_sink = learning_sink

    async def delegate(
        self,
        *,
        auth: AuthContext,
        session: SessionState,
        request_id: UUID,
        utterance: str,
        budget: TurnBudget,
        ledger: EvidenceLedger,
        request: TutorRequest,
        trace: TurnTrace,
    ) -> TutorDelegation:
        refs = []
        for evidence_id in request.evidence_ids:
            evidence = ledger.evidence.get(evidence_id)
            if evidence is None:
                raise HandoffRejectedError("handoff_evidence_not_validated")
            if evidence.evidence_version is None:
                raise HandoffRejectedError("evidence_version_unavailable")
            refs.append(
                EvidenceRef(
                    evidence_id=evidence.evidence_id,
                    source_version_id=evidence.source_version_id,
                    evidence_version=evidence.evidence_version,
                )
            )

        summaries: list[AssessmentSummary] = []
        if self._summaries is not None:
            summaries = list(await self._summaries.summaries(auth, request.target_concept_ids))[:12]

        pending = session.pending_question
        handoff = CoordinatorToTutorHandoff(
            handoff_id=uuid4(),
            request_id=request_id,
            session_id=session.session_id,
            lesson_id=session.active_lesson.lesson_id if session.active_lesson else uuid4(),
            mode=request.mode,
            learning_goal=request.learning_goal,
            original_utterance=utterance,
            target_concept_ids=list(dict.fromkeys(request.target_concept_ids)),
            explanation_level=request.explanation_level,
            evidence_refs=refs,
            recent_dialogue=await self._recent_dialogue(session.session_id),
            assessment_summaries=summaries,
            pending_question=(
                PendingQuestion(
                    question_id=pending.question_id,
                    question_version=pending.question_version,
                    hints_used=pending.hints_used,
                )
                if pending
                else None
            ),
            deadline_at=budget.deadline_at,
        )
        state = build_turn_state(handoff, auth, budget)
        trace.record(
            "handoff_sent",
            handoff_id=handoff.handoff_id,
            mode=handoff.mode,
            evidence_ids=[ref.evidence_id for ref in refs],
            summaries_supplied=len(summaries),
        )

        raw = await self._run_bounded(state, budget)
        result = self._validate(handoff, raw)
        question = await self._validate_question(auth, result)

        committed = [event for event in result.proposed_learning_events if event.attempt_id]
        uncommitted = [event for event in result.proposed_learning_events if not event.attempt_id]
        for event in result.proposed_learning_events:
            if event.event_type == "review_requested":
                trace.record("handoff_result", legacy_event_ignored="review_requested")
        if committed:
            # Already committed by the Learning service inside the Tutor turn.
            trace.record("handoff_result", already_committed_attempts=len(committed))
        if self._learning_sink is not None and uncommitted:
            await self._learning_sink.forward(auth, handoff.handoff_id, result.model_copy(update={"proposed_learning_events": uncommitted}))
            trace.record("learning_proposal_forwarded", count=len(uncommitted))

        trace.record(
            "handoff_result",
            handoff_id=handoff.handoff_id,
            status=result.status,
            segments=len(result.public_segments),
            evidence_ids=result.evidence_ids,
        )
        return TutorDelegation(handoff=handoff, result=result, question=question)

    async def _recent_dialogue(self, session_id: UUID) -> list[DialogueTurn]:
        if self._dialogue is None:
            return []
        entries = await self._dialogue.recent(session_id, 16)
        turns = [
            DialogueTurn(role=entry.role, content=entry.content[:4000])
            for entry in entries
            if entry.role in ("student", "tutor")
        ]
        return turns[-8:]

    async def _run_bounded(self, state: TutorTurnState, budget: TurnBudget) -> Any:
        remaining = budget.remaining_seconds()
        if remaining <= 0 or budget.cancelled:
            raise TurnCancelledError("no time left for delegation") if budget.cancelled else ResourceUnavailableError("deadline reached")
        run = asyncio.ensure_future(self._runner.run(state))
        cancel_waiter = asyncio.ensure_future(budget.wait_cancelled())
        try:
            done, _ = await asyncio.wait({run, cancel_waiter}, timeout=remaining, return_when=asyncio.FIRST_COMPLETED)
        finally:
            cancel_waiter.cancel()
            if not run.done():
                # Deadline, STOP, or this turn task itself being cancelled (a
                # disconnect cancels it directly): a Tutor run persists
                # questions and commits attempts, so it must not outlive its turn.
                run.cancel()
                await asyncio.gather(run, return_exceptions=True)
        if run in done and isinstance(run.exception(), NotImplementedError):
            # A Tutor capability blocked on an unmade decision is a pending
            # capability, not a crash and not a teaching result.
            raise HandoffRejectedError("tutor_capability_pending_decision")
        if run not in done:
            if budget.cancelled:
                raise TurnCancelledError("turn cancelled during delegation")
            raise ResourceUnavailableError("tutor did not finish before the deadline")
        if budget.cancelled:
            raise TurnCancelledError("turn cancelled during delegation")
        return run.result()

    @staticmethod
    def _validate(handoff: CoordinatorToTutorHandoff, raw: Any) -> TutorToCoordinatorResult:
        try:
            data = raw.model_dump(mode="json") if hasattr(raw, "model_dump") else raw
            result = TutorToCoordinatorResult.model_validate(data)
        except ValidationError as exc:
            raise HandoffRejectedError("tutor_result_schema_invalid") from exc
        if result.handoff_id != handoff.handoff_id:
            raise HandoffRejectedError("tutor_result_handoff_mismatch")
        allowed = {ref.evidence_id for ref in handoff.evidence_refs}
        if not set(result.evidence_ids) <= allowed:
            raise HandoffRejectedError("tutor_cited_unhanded_evidence")
        if result.status == "awaiting_student_answer" and not result.pending_question_id:
            raise HandoffRejectedError("awaiting_answer_without_question")
        return result

    async def _validate_question(self, auth: AuthContext, result: TutorToCoordinatorResult) -> Optional[PlannedQuestion]:
        if result.status != "awaiting_student_answer":
            return None
        if self._pending_questions is None:
            raise HandoffRejectedError("pending_question_storage_unavailable")
        approved = await maybe_await(self._pending_questions.get_pending(auth, result.pending_question_id))
        if approved is None:
            raise HandoffRejectedError("question_not_persisted_before_delivery")

        # Only a short answer's key is answer text. For choice kinds it is an
        # option id ("a", "true") and the options are public anyway; matching
        # the id would flag "2 A" (amperes) as a leak of option "a".
        correct = getattr(getattr(approved, "answer_key", None), "correct_answer", None)
        if approved.kind == QuestionKind.SHORT_ANSWER and correct and len(correct.strip()) > 0:
            pattern = re.compile(r"(?<!\w)" + re.escape(correct.strip()) + r"(?!\w)", re.IGNORECASE)
            for segment in result.public_segments:
                if pattern.search(segment.text):
                    raise HandoffRejectedError("private_answer_in_public_segment")
        return public_question_plan(approved, hints_used=0)
