"""Tutor agent entry point.

Netra's second and only other reasoning agent (CLAUDE.md "Architecture:
only two agents" — do not add a third). Receives exactly one typed
CoordinatorToTutorHandoff per turn and returns exactly one
TutorToCoordinatorResult; never free-form agent-to-agent chat, never the
Coordinator's full history (CLAUDE.md "Coordinator <-> Tutor
communication must use the versioned typed handoff schema ... Never
implement free-form agent-to-agent chat. Never pass private
chain-of-thought between agents."). Bounded the same way as a
Coordinator turn: a maximum step count, deadline, cancellation path, and
explicit stop condition (CLAUDE.md "Agent loops must always have ...").

Collaborators arrive as TutorServices rather than being constructed
here: the Tutor resolves evidence, persists questions and commits
assessment history only through bounded services it is handed
(agent-boundaries.md: "Agents receive bounded services/tools, never raw
database connections, credentials or unrestricted executors"). Every
Protocol in TutorServices already existed; this module wires them into a
turn, it does not define new boundaries.

What this loop deliberately does NOT do:

- It never commits a learning event itself. It calls
  LearningService.propose_event as a bounded tool and reports the
  resulting attempt_id (learning.md: "Tutor proposes learning events or
  grades. Learning service validates ownership, question identity,
  evidence, rubric, response finality and replay identity before
  committing"). ProposedLearningEvent.attempt_id is the contract field
  that exists for exactly this.
- It never proposes, derives or reports a mastery label. Automatic
  learning labels are a removed requirement (current-scope.md) and
  ProposedLearningEvent has no status field to carry one.
- It never grades an utterance that is not a recognisable attempt at the
  pending question. A declined, skipped, misheard or corrected response
  produces a clarification and leaves the pending question unchanged —
  never an attempt (current-scope.md: "a declined check must not create
  an answer or grade"; docs/team/M4.md: "correction is not automatically
  failure").
- It never fabricates content for evidence that did not resolve. Nothing
  resolved means status "needs_more_evidence", not an invented answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Callable, Optional, Protocol, Sequence
from uuid import NAMESPACE_URL, uuid5

from netra_api.content.retrieval.evidence import Evidence, EvidenceResolution, EvidenceResolver
from netra_api.coordinator.handoff import (
    CoordinatorToTutorHandoff,
    ProposedLearningEvent,
    PublicSegment,
    TutorToCoordinatorResult,
)
from netra_api.coordinator.limits import TurnBudget
from netra_api.learning.assessment.grader import (
    DETERMINISTIC_QUESTION_KINDS,
    grade_objective_answer,
    resolve_submitted_option,
)
from netra_api.learning.assessment.models import (
    AnswerSubmission,
    AssessmentAttempt,
    AttemptOutcome,
    LearningEventProposal,
)
from netra_api.learning.assessment.service import LearningService
from netra_api.learning.quiz.generator import QuizGenerationRequest, QuizGenerator
from netra_api.learning.quiz.models import ApprovedQuestion, QuestionDraft
from netra_api.learning.quiz.repository import PendingQuestionRepository
from netra_api.learning.quiz.validator import (
    QuestionValidationError,
    bind_draft_to_evidence,
    evidence_refs_for,
    validate_question_for_approval,
)
from netra_api.learning.tutor.evidence_versions import SourceVersionComparison, compare_source_version
from netra_api.learning.tutor.policies import assert_not_coordinator_state
from netra_api.learning.tutor.providers.groq import GroqTutorModelConfig, GroqTutorProvider
from netra_api.learning.tutor.state import TutorTurnState
from netra_api.platform.auth_context import AuthContext
from netra_api.platform.awaitables import maybe_await
from netra_api.platform.errors import AuthorizationError, NetraError, TurnBudgetExceededError

MAX_EVIDENCE_EXCERPT_CHARS = 2000
"""How much of one resolved Evidence body is placed in a prompt.

A local prompt-assembly bound, NOT an approved product or protocol
value: the authorities require bounded agent context ("Receive bounded
relevant dialogue, the original utterance and authorized evidence IDs")
without specifying a number, and nothing downstream reads this. Raising
or lowering it changes only how much source text one model call sees.
"""

MAX_PUBLIC_SEGMENT_CHARS = 8000
"""Mirrors PublicSegment.text's contract maximum, so an over-long model
response is truncated here rather than failing contract validation after
the work is already done."""

MAX_DECISION_SUMMARY_CHARS = 1000
"""Mirrors TutorToCoordinatorResult.decision_summary's contract maximum."""

MAX_HISTORY_ATTEMPTS_IN_CONTEXT = 5
"""How many prior attempts are summarised into a lesson prompt.

A local prompt-assembly bound, NOT approved product policy. Bounded
because agent context must be bounded; the exact number is not a
protocol or product value and nothing downstream reads it.
"""

TUTOR_INSTRUCTION_PATH = Path(__file__).resolve().parent / "prompts" / "tutor.md"

GroundingValidator = Callable[[QuestionDraft, Sequence[Evidence]], None]
"""Signature of the check a draft must pass before it may be approved.
Raises when the draft may not be approved; returns None when it may."""


@lru_cache(maxsize=1)
def load_tutor_instruction() -> str:
    """Read the Tutor runtime instruction from prompts/tutor.md.

    The prompt file is the owned runtime artifact; this is the path that
    actually loads it, so the instruction cannot silently be empty in
    production. Cached because it is immutable for the process lifetime.
    """

    return TUTOR_INSTRUCTION_PATH.read_text(encoding="utf-8")


@dataclass(frozen=True)
class TutorServices:
    """The bounded collaborators one Tutor turn may use.

    A plain dataclass rather than a Pydantic model for the same reason
    netra_api.coordinator.limits.TurnBudget is: these are runtime
    collaborators, not a wire shape. Every field is a Protocol that
    already existed before this loop did.
    """

    provider: GroqTutorProvider
    evidence_resolver: EvidenceResolver
    pending_questions: PendingQuestionRepository
    learning_service: LearningService
    quiz_generator: Optional[QuizGenerator] = None
    """Only the check_understanding mode needs one; a caller that never
    requests a check need not supply it."""
    model_config: GroqTutorModelConfig = field(default_factory=GroqTutorModelConfig)
    instruction: str = field(default_factory=lambda: load_tutor_instruction())
    """The Tutor runtime instruction (prompts/tutor.md).

    Loaded by default rather than defaulting to "": an empty instruction
    silently strips every teaching, grounding and answer-key-privacy rule
    from the prompt, and nothing downstream would report that the Tutor
    was running unguided. Tests override it with a short literal.
    """
    grounding_validator: GroundingValidator = validate_question_for_approval
    """Injection seam for the OPEN evidence-grounding decision (D2).

    Defaults to the production
    netra_api.learning.quiz.validator.validate_question_for_approval,
    which fails closed because validate_draft_is_grounded is deliberately
    unimplemented. Tests substitute an approving validator to exercise the
    question-delivery behaviour *downstream* of grounding.

    Substituting this does NOT implement grounding and must never be
    described as such: production keeps the fail-closed default until the
    M3/M4 decision lands.

    Reference binding (netra_api.learning.quiz.validator.bind_draft_to_evidence)
    runs before this seam and cannot be substituted: the validator only
    ever receives the evidence the draft is bound to.
    """


class TutorAgent(Protocol):
    """Bounded entry point: one handoff in, one TutorToCoordinatorResult out."""

    async def handle(self, handoff: CoordinatorToTutorHandoff) -> TutorToCoordinatorResult:
        ...


def build_turn_state(
    handoff: CoordinatorToTutorHandoff, auth: AuthContext, budget: TurnBudget
) -> TutorTurnState:
    """Construct the Tutor's own scoped state for one handoff.

    The only place a Tutor turn is seeded from: it takes the handoff
    CLAUDE.md requires and nothing else, so there is no parameter here
    through which Coordinator-internal state could pass (see
    netra_api.learning.tutor.policies.assert_not_coordinator_state).

    budget must be the originating Coordinator turn's budget, so
    delegated work spends the same model-decision, tool-call and deadline
    allowance (CLAUDE.md: "Handoff and delegated work consume the same
    originating budgets and deadline"). Passing a fresh TurnBudget() here
    would hand the Tutor a second full allowance and a restarted clock,
    so the deadline is checked against the handoff rather than trusted.
    """

    if budget.deadline_at > handoff.deadline_at:
        raise TurnBudgetExceededError(
            f"Tutor budget expires at {budget.deadline_at.isoformat()}, "
            f"after the originating turn's deadline of {handoff.deadline_at.isoformat()}; "
            "delegated work must inherit the originating budget, not restart it"
        )

    return TutorTurnState(handoff=handoff, auth=auth, budget=budget)


async def run_turn(state: TutorTurnState, services: TutorServices) -> TutorToCoordinatorResult:
    """Execute one bounded Tutor turn and return its result.

    Dispatches on state.handoff.mode. Every step checks the shared
    originating budget before dispatching (coordinator.md: "Check
    cancellation, remaining time, permissions and quota before
    dispatch"), and budget exhaustion produces the bounded failure
    response that rule requires rather than escaping as an exception.

    The one exception deliberately allowed to escape is the
    NotImplementedError raised by
    netra_api.learning.quiz.validator.validate_draft_is_grounded on the
    check_understanding path. TutorToCoordinatorResult has no field that
    can express "blocked on an unmade product decision", so collapsing
    that into status="failed" would make an known architectural gap
    indistinguishable from a provider outage. See the module docstring of
    that validator: the definition of evidence support is an open M3/M4
    decision, and guessing it "would make it policy by default".
    """

    assert_not_coordinator_state(state)

    try:
        _ensure_can_continue(state)

        if state.handoff.mode == "continue_lesson" and state.handoff.pending_question is not None:
            # Continuing a lesson while a question is outstanding is the
            # Coordinator saying the student wants help with it, not that
            # they answered. A hint keeps the question pending.
            return await _run_hint(state, services)
        if state.handoff.mode in ("explain", "continue_lesson"):
            return await _run_explanation(state, services)
        if state.handoff.mode == "check_understanding":
            return await _run_check_understanding(state, services)
        return await _run_evaluate_answer(state, services)
    except TurnBudgetExceededError as exhausted:
        return _result(
            state,
            status="failed",
            decision_summary=f"Turn budget exhausted before completion: {exhausted}",
        )
    except AuthorizationError:
        # Never converted into a result. An authorization denial is not a
        # teaching outcome, and reporting it as an ordinary failed turn
        # would bury a security-relevant event in a status field.
        raise
    except NetraError as failure:
        # A bounded failure for an adapter or service fault (provider
        # outage, unavailable dependency). Only the exception's type is
        # reported: its message may carry provider payload detail, which
        # must not ride out on the result (message-flow.md's error rules —
        # "never a stack trace, SQL detail, or provider payload").
        return _result(
            state,
            status="failed",
            decision_summary=f"Turn failed: {type(failure).__name__}.",
        )


# --- mode handlers ---------------------------------------------------------


async def _run_explanation(state: TutorTurnState, services: TutorServices) -> TutorToCoordinatorResult:
    """explain / continue_lesson: teach from resolved evidence, answer the question."""

    evidence = await _resolve_evidence(state, services)
    if not evidence:
        return _result(
            state,
            status="needs_more_evidence",
            decision_summary="No supplied evidence reference resolved to authorized content.",
        )

    history = await _select_relevant_history(state, services)
    text = await _decide(
        state,
        services,
        _build_explanation_prompt(state.handoff, services.instruction, evidence, history),
    )
    if not text:
        return _result(state, status="failed", decision_summary="Model returned no usable explanation text.")

    return _result(
        state,
        status="completed",
        segments=[PublicSegment(kind="explanation", text=text[:MAX_PUBLIC_SEGMENT_CHARS])],
        evidence=evidence,
        events=_exposure_events(state.handoff),
    )


async def _run_hint(state: TutorTurnState, services: TutorServices) -> TutorToCoordinatorResult:
    """Give one targeted, evidence-grounded hint for the pending question.

    Assistance, not assessment: the question stays pending, no attempt is
    recorded, and the student keeps their opportunity to answer. The hint
    is attributed to the question it belongs to through the preserved
    pending_question_id, and to the eventual attempt through
    PendingQuestionRef.hints_used, which the Session service increments
    and which _run_evaluate_answer copies onto the committed attempt — so
    an assisted attempt stays distinguishable from an independent one
    (learning.md: "Preserve assisted versus independent attempts").

    The answer key and rubric are structurally excluded from the hint
    prompt: _build_hint_prompt receives the question's public prompt text,
    never the ApprovedQuestion. A hint cannot leak a reference answer that
    was never put in front of the model.
    """

    pending_ref = state.handoff.pending_question
    assert pending_ref is not None  # guarded by the caller's dispatch

    _ensure_can_continue(state)
    state.budget.register_tool_call()
    question = await maybe_await(services.pending_questions.get_pending(state.auth, pending_ref.question_id))
    if question is None:
        return _result(
            state,
            status="failed",
            decision_summary="The referenced question is not pending for this account.",
        )
    if question.question_version != pending_ref.question_version:
        return _result(
            state,
            status="failed",
            decision_summary=(
                f"Pending question is at version {question.question_version}; "
                f"the handoff targets version {pending_ref.question_version}."
            ),
        )

    evidence = await _resolve_evidence(state, services)
    if not evidence:
        return _result(
            state,
            status="needs_more_evidence",
            decision_summary="No supplied evidence reference resolved; cannot ground a hint.",
        )

    text = await _decide(
        state,
        services,
        _build_hint_prompt(
            state.handoff, services.instruction, evidence, question.prompt, pending_ref.hints_used
        ),
    )
    if not text:
        return _result(state, status="failed", decision_summary="Model returned no usable hint text.")

    return _result(
        state,
        status="awaiting_student_answer",
        segments=[PublicSegment(kind="hint", text=text[:MAX_PUBLIC_SEGMENT_CHARS])],
        evidence=evidence,
        # Proposed on the wire only. Committing assistance durably needs
        # the factual-history record that AssessmentAttempt cannot
        # represent (docs/team/handoffs/M4.md, D3); the Session service
        # owns the hints_used counter in the meantime.
        events=[ProposedLearningEvent(event_type="hint_used", concept_id=question.concept_id)],
        pending_question_id=question.question_id,
        decision_summary="Delivered one hint; question remains pending and no attempt was recorded.",
    )


async def _run_check_understanding(
    state: TutorTurnState, services: TutorServices
) -> TutorToCoordinatorResult:
    """check_understanding: offer an optional, evidence-grounded question.

    Ordering matters and is required, not incidental: the draft is
    validated (structure AND evidence grounding) before it is persisted,
    and persisted before its text is ever placed in a public segment
    (learning.md: "Persist the pending question before delivering it to
    the client"). A question the student could be asked but the server
    could not later resolve is exactly what that rule prevents.

    Blocked today at validate_question_for_approval — see run_turn's
    docstring.
    """

    if services.quiz_generator is None:
        return _result(
            state,
            status="failed",
            decision_summary="check_understanding requires a QuizGenerator; none was supplied.",
        )

    evidence = await _resolve_evidence(state, services)
    if not evidence:
        return _result(
            state,
            status="needs_more_evidence",
            decision_summary="No supplied evidence reference resolved to authorized content.",
        )

    concept_id = _primary_concept_id(state.handoff)
    if concept_id is None:
        return _result(
            state,
            status="failed",
            decision_summary="check_understanding requires at least one target concept.",
        )

    _ensure_can_continue(state)
    state.budget.register_model_decision()
    draft: QuestionDraft = await services.quiz_generator.generate(
        QuizGenerationRequest(
            concept_id=concept_id,
            explanation_level=state.handoff.explanation_level,
            evidence_refs=list(state.handoff.evidence_refs),
        )
    )

    # The draft's concept is model output. It must be the concept that was
    # requested, or the question — and every attempt and projection that
    # later hangs off it — would be filed under a concept the handoff never
    # targeted (learning.md: "Do not silently create concepts ... from
    # Tutor output").
    if draft.concept_id != concept_id:
        raise QuestionValidationError("question draft targets a concept that was not requested")

    # Reference binding is always enforced here and is not part of the
    # injectable seam: a draft citing nothing, or citing evidence this turn
    # did not resolve, is refused (UngroundedDraftError, a bounded failed
    # result) no matter which grounding validator is installed.
    bound = bind_draft_to_evidence(draft, evidence)

    # Structural validation AND the support check, over only the evidence
    # the draft cites. The default validator is the production one, which
    # fails closed on the open support decision; nothing below runs until
    # that decision lands.
    services.grounding_validator(draft, bound)

    question = ApprovedQuestion(
        question_id=_derive_question_id(state.handoff, concept_id),
        question_version=1,
        concept_id=draft.concept_id,
        kind=draft.kind,
        prompt=draft.prompt,
        options=list(draft.options),
        answer_key=draft.answer_key,
        created_at=datetime.now(timezone.utc),
        evidence_refs=evidence_refs_for(bound),
    )

    _ensure_can_continue(state)
    state.budget.register_tool_call()
    persisted = await maybe_await(services.pending_questions.persist_pending(state.auth, question))

    return _result(
        state,
        status="awaiting_student_answer",
        # Only the public prompt crosses this boundary. persisted.answer_key
        # is never read here, so there is no path by which it reaches a
        # public segment (learning.md "Quiz privacy").
        segments=[PublicSegment(kind="question", text=persisted.prompt[:MAX_PUBLIC_SEGMENT_CHARS])],
        # Only what the question is bound to: citing every resolved item
        # would claim support from evidence the question never used.
        evidence=bound,
        events=_exposure_events(state.handoff),
        pending_question_id=persisted.question_id,
    )


async def _run_evaluate_answer(
    state: TutorTurnState, services: TutorServices
) -> TutorToCoordinatorResult:
    """evaluate_answer: grade a finalized attempt at the pending question.

    Only a substantive, recognisable attempt at the *persisted* question
    is assessable (learning.md: "Only a substantive finalized answer to
    an existing question is assessable"). Everything else — a declined
    check, a request for a hint, a misrecognised utterance, a transcript
    correction — returns a clarification with the pending question left
    exactly as it was, and records nothing.
    """

    pending_ref = state.handoff.pending_question
    if pending_ref is None:
        return _result(
            state,
            status="failed",
            decision_summary="evaluate_answer requires handoff.pending_question; none was supplied.",
        )

    # Replay check FIRST, before any grading work. A retransmitted turn
    # carries the same request_id, so the attempt it already committed is
    # findable up front (message-flow.md: "Replayed proposals must not
    # append another attempt"). Without this the retransmission would find
    # the question no longer pending — propose_event cleared it via
    # mark_answered — and report a spurious failure for a turn that
    # actually succeeded. Checking here also means a replay costs no model
    # decision and no second grade.
    _ensure_can_continue(state)
    state.budget.register_tool_call()
    already_committed = await maybe_await(
        services.learning_service.find_committed_attempt(
            state.auth, pending_ref.question_id, pending_ref.question_version
        )
    )
    if already_committed is not None:
        return _replayed_result(state, already_committed)

    _ensure_can_continue(state)
    state.budget.register_tool_call()
    question = await maybe_await(services.pending_questions.get_pending(state.auth, pending_ref.question_id))
    if question is None:
        return _result(
            state,
            status="failed",
            decision_summary="The referenced question is not pending for this account.",
        )
    if question.question_version != pending_ref.question_version:
        return _result(
            state,
            status="failed",
            decision_summary=(
                f"Pending question is at version {question.question_version}; "
                f"the handoff targets version {pending_ref.question_version}."
            ),
        )

    # Preserved verbatim; the Tutor never rewrites what the student said
    # (coordinator.md: "Preserve the original utterance").
    answer_text = state.handoff.original_utterance

    if not answer_text.strip():
        return _awaiting_clarification(state, question.question_id, "I didn't catch an answer there.")

    # Only the rubric path resolves evidence; a choice-kind answer is
    # graded against the stored answer key, so citing evidence it never
    # read would be a citation nothing supports.
    used_evidence: list[Evidence] = []

    if question.kind in DETERMINISTIC_QUESTION_KINDS:
        outcome, feedback, evaluated_by = _grade_deterministically(question, answer_text)
        if outcome is None:
            # Resolved to no option: the utterance may be a decline, an
            # aside, or a misrecognition. Grading it INCORRECT here would
            # turn "correction is not automatically failure" into exactly
            # that, so it is not graded at all.
            return _awaiting_clarification(
                state,
                question.question_id,
                "I couldn't match that to one of the choices. Could you say which one you mean?",
            )
    else:
        used_evidence = await _resolve_evidence(state, services)
        if not used_evidence:
            return _result(
                state,
                status="needs_more_evidence",
                decision_summary="No supplied evidence reference resolved; cannot ground feedback.",
            )
        raw = await _decide(
            state,
            services,
            _build_grading_prompt(
                state.handoff, services.instruction, used_evidence, question, answer_text
            ),
        )
        verdict, feedback = _parse_verdict(raw)
        if verdict is None or verdict == "NOT_AN_ANSWER" or not feedback:
            # Unparseable output is treated exactly like a non-answer:
            # fail closed, record nothing, keep the question pending.
            return _awaiting_clarification(
                state,
                question.question_id,
                feedback
                or "Let's stay with the question — could you tell me what you think the answer is?",
            )
        outcome = _VERDICT_OUTCOMES[verdict]
        evaluated_by = "tutor"

    _ensure_can_continue(state)
    state.budget.register_tool_call()
    attempt = await maybe_await(services.learning_service.propose_event(
        state.auth,
        LearningEventProposal(
            # auth.account_id, never a model- or handoff-supplied account
            # (CLAUDE.md: "An account ID ... supplied by a model/client is
            # not authority").
            account_id=state.auth.account_id,
            concept_id=question.concept_id,
            event_type="answer_evaluated",
            question_id=question.question_id,
            question_version=question.question_version,
            # Only final_text is set: the handoff carries no ASR or
            # correction metadata, so claiming original_transcript or
            # corrected_text here would assert provenance nothing
            # established. See docs/team/handoffs/M4.md.
            answer=AnswerSubmission(final_text=answer_text),
            outcome=outcome,
            evaluated_by=evaluated_by,
            hints_used=pending_ref.hints_used,
        ),
    ))

    return _result(
        state,
        status="completed",
        segments=[
            PublicSegment(
                kind="explanation" if outcome == AttemptOutcome.CORRECT else "correction",
                text=feedback[:MAX_PUBLIC_SEGMENT_CHARS],
            )
        ],
        evidence=used_evidence,
        events=[
            ProposedLearningEvent(
                event_type="answer_evaluated",
                concept_id=question.concept_id,
                attempt_id=str(attempt.attempt_id),
            )
        ],
    )


# --- bounded steps ---------------------------------------------------------


@dataclass(frozen=True)
class RelevantHistory:
    """Factual prior attempts for this lesson's concepts, plus whether the
    history service could be reached at all.

    `available` is not a convenience flag. learning.md: "An unavailable
    history service is not evidence of no history." Collapsing a failed
    lookup into an empty list would let the Tutor teach as though the
    student had never attempted anything — a negative claim the system
    never established. The two states are therefore kept distinct all the
    way into the prompt text.
    """

    attempts: tuple[AssessmentAttempt, ...]
    available: bool


async def _select_relevant_history(state: TutorTurnState, services: TutorServices) -> RelevantHistory:
    """Read committed attempts for the handoff's target concepts.

    Returns facts (what was answered, what outcome was recorded, how much
    assistance was used), never a derived label — automatic mastery
    labelling is a removed requirement, so nothing here calls
    derive_status_from_history.

    An AuthorizationError is deliberately NOT swallowed: that would be a
    real defect in how this turn was scoped, not an unavailable service.
    """

    if not state.handoff.target_concept_ids:
        return RelevantHistory(attempts=(), available=True)

    _ensure_can_continue(state)
    state.budget.register_tool_call()
    try:
        attempts = await maybe_await(
            services.learning_service.list_attempts_for_concepts(
                state.auth, list(state.handoff.target_concept_ids)
            )
        )
    except (AuthorizationError, TurnBudgetExceededError):
        # Neither is a history-availability signal: the first is a scoping
        # defect and the second is turn control flow. Only a genuine
        # history-service failure becomes available=False.
        raise
    except NetraError:
        return RelevantHistory(attempts=(), available=False)

    return RelevantHistory(
        attempts=tuple(attempts[-MAX_HISTORY_ATTEMPTS_IN_CONTEXT:]), available=True
    )


def _format_history(history: RelevantHistory) -> str:
    """Render history as exact facts, keeping "unknown" distinct from "none".

    Exact canonical facts stay outside any summary (current-scope.md), so
    each attempt contributes the student's own answer text and the
    recorded outcome rather than a characterisation of them.
    """

    if not history.available:
        return (
            "Earlier attempt history could not be retrieved for this turn. "
            "Do not assume the student has, or has not, worked on this before, "
            "and do not refer to their history."
        )
    if not history.attempts:
        return "No earlier attempts are recorded for these concepts."

    lines = []
    for attempt in history.attempts:
        assistance = (
            f", after {attempt.hints_used} hint(s)" if attempt.hints_used else ", unassisted"
        )
        lines.append(
            f'- On question {attempt.question_id} the student answered '
            f'"{attempt.answer.final_text}"; recorded outcome: {attempt.outcome.value}{assistance}.'
        )
    return "Earlier recorded attempts (facts, not conclusions about the student):\n" + "\n".join(lines)


def _ensure_can_continue(state: TutorTurnState) -> None:
    """Cancellation/deadline/counter check before every dispatch."""

    if state.budget.should_stop():
        raise TurnBudgetExceededError("turn budget is exhausted, expired or cancelled")


async def _resolve_evidence(state: TutorTurnState, services: TutorServices) -> list[Evidence]:
    """Resolve the handoff's evidence references through the authorized service.

    An agent-supplied body under an evidence ID is never trusted
    (agent-boundaries.md); only what the resolver authorizes against
    PostgreSQL is used, and unresolved references simply do not appear.

    Resolved evidence is then checked against the source version AND the
    evidence version the handoff declared for it (see
    netra_api.learning.tutor.evidence_versions). Evidence whose resolved
    identity differs from, or cannot be compared with, the declared one is
    dropped exactly like an unresolved reference: the Tutor never teaches
    from a version the handoff was not built against.
    """

    _ensure_can_continue(state)
    state.budget.register_tool_call()
    refs = list(state.handoff.evidence_refs)
    resolutions = await maybe_await(
        services.evidence_resolver.resolve(state.auth, [ref.evidence_id for ref in refs])
    )
    if len(resolutions) != len(refs):
        # The resolver contract is one resolution per requested id, in
        # order. Anything else means refs and resolutions cannot be paired,
        # so no declared version can be checked: use nothing.
        return []

    accepted: list[Evidence] = []
    for ref, evidence in zip(refs, _resolved_or_none(resolutions)):
        if evidence is None or evidence.evidence_id != ref.evidence_id:
            continue
        if compare_source_version(ref, evidence) is SourceVersionComparison.MATCH:
            accepted.append(evidence)
    return accepted


def _resolved_or_none(resolutions: Sequence[EvidenceResolution]) -> list[Optional[Evidence]]:
    """Resolutions narrowed to their evidence, keeping positions aligned."""

    return [resolution.evidence for resolution in resolutions]


async def _decide(state: TutorTurnState, services: TutorServices, prompt: str) -> str:
    """One model decision against the shared originating budget."""

    _ensure_can_continue(state)
    state.budget.register_model_decision()
    decision = await services.provider.decide(services.model_config, prompt)
    return decision.raw_text.strip()


def _grade_deterministically(
    question: ApprovedQuestion, answer_text: str
) -> tuple[Optional[AttemptOutcome], str, str]:
    """Grade a choice-kind answer without a model call.

    Returns (None, ...) when the utterance matches no option — that is a
    "not recognisably an answer" signal for the caller, not a wrong
    answer. netra_api.learning.assessment.grader.grade_objective_answer
    is only reached once an option actually matched, so its documented
    "unmatched is INCORRECT" rule still holds for real grading and is not
    changed here.
    """

    if resolve_submitted_option(question, answer_text) is None:
        return None, "", "grader"

    outcome = grade_objective_answer(question, AnswerSubmission(final_text=answer_text))
    feedback = (
        "That's right."
        if outcome == AttemptOutcome.CORRECT
        else "That isn't the one — let's look at why."
    )
    return outcome, feedback, "grader"


_VERDICT_OUTCOMES = {
    "CORRECT": AttemptOutcome.CORRECT,
    "PARTIAL": AttemptOutcome.PARTIAL,
    "INCORRECT": AttemptOutcome.INCORRECT,
}
_NOT_AN_ANSWER = "NOT_AN_ANSWER"


def _parse_verdict(raw_text: str) -> tuple[Optional[str], str]:
    """Split a grading response into (verdict token, student-facing feedback).

    Strict by design: the first line must be exactly one known token, and
    the verdict line is never delivered to the student. Anything else
    returns (None, ...) so the caller fails closed rather than inferring a
    grade from prose — a misread verdict writes a wrong fact into
    append-only assessment history.

    This is a STRUCTURAL check only. A parsed verdict means the model
    answered in the required shape; it establishes nothing about whether
    the grade or the feedback is actually supported by the source. Nothing
    in this module can establish that — it needs source review against the
    original material and the open evidence-grounding decision (see
    docs/team/handoffs/M4.md). Do not read a successful parse as evidence
    of a correct assessment.
    """

    lines = raw_text.strip().splitlines()
    if not lines:
        return None, ""

    token = lines[0].strip().upper()
    if token not in _VERDICT_OUTCOMES and token != _NOT_AN_ANSWER:
        return None, ""

    return token, "\n".join(lines[1:]).strip()


# --- result assembly -------------------------------------------------------


def _result(
    state: TutorTurnState,
    *,
    status: str,
    segments: Sequence[PublicSegment] = (),
    evidence: Sequence[Evidence] = (),
    events: Sequence[ProposedLearningEvent] = (),
    pending_question_id: Optional[str] = None,
    decision_summary: Optional[str] = None,
) -> TutorToCoordinatorResult:
    """Build the single result this turn returns.

    decision_summary is a short observable summary, never private chain of
    thought (agent-boundaries.md: "A short decision summary is not private
    chain-of-thought").
    """

    seen: list[str] = []
    for item in evidence:
        if item.evidence_id not in seen:
            seen.append(item.evidence_id)

    return TutorToCoordinatorResult(
        handoff_id=state.handoff.handoff_id,
        status=status,
        public_segments=list(segments),
        pending_question_id=pending_question_id,
        evidence_ids=seen,
        proposed_learning_events=list(events),
        decision_summary=decision_summary[:MAX_DECISION_SUMMARY_CHARS] if decision_summary else None,
    )


def _replayed_result(state: TutorTurnState, attempt: AssessmentAttempt) -> TutorToCoordinatorResult:
    """Report an attempt this turn already committed, without re-committing.

    The same attempt_id goes back out, so the Coordinator sees the effect
    of the original submission rather than a second one. No feedback text
    is reproduced: the feedback was delivered with the first response and
    is not stored, and inventing replacement wording here would be
    fabricating teaching content the student never received.
    """

    return _result(
        state,
        status="completed",
        events=[
            ProposedLearningEvent(
                event_type="answer_evaluated",
                concept_id=attempt.concept_id,
                attempt_id=str(attempt.attempt_id),
            )
        ],
        decision_summary="Replay of an already-committed attempt; nothing was committed again.",
    )


def _awaiting_clarification(
    state: TutorTurnState, question_id: str, text: str
) -> TutorToCoordinatorResult:
    """Keep the existing pending question and record nothing.

    The same persisted question_id goes back out, so "return to question"
    and reconnect restore the question the student was actually asked
    rather than a newly generated one (coordinator.md: "Return to question
    restores the existing pending question, not a newly generated one").
    """

    return _result(
        state,
        status="awaiting_student_answer",
        segments=[PublicSegment(kind="question", text=text)],
        pending_question_id=question_id,
        decision_summary="Utterance was not a recognisable attempt; no attempt recorded.",
    )


def _exposure_events(handoff: CoordinatorToTutorHandoff) -> list[ProposedLearningEvent]:
    """Propose one concept_exposed event per target concept.

    Reported on the wire only — deliberately NOT sent to
    LearningService.propose_event, which rejects every event type but
    answer_evaluated because AssessmentAttempt cannot represent
    non-answer activity (see that service's UnrepresentableLearningEventError).
    Surfacing the proposal keeps the delivered-activity record visible as
    the gap it is instead of silently dropping it; committing it is
    blocked on the factual-history schema decision in
    docs/team/handoffs/M4.md.
    """

    return [
        ProposedLearningEvent(event_type="concept_exposed", concept_id=concept_id)
        for concept_id in handoff.target_concept_ids
    ]


def _primary_concept_id(handoff: CoordinatorToTutorHandoff) -> Optional[str]:
    return handoff.target_concept_ids[0] if handoff.target_concept_ids else None


def _derive_question_id(handoff: CoordinatorToTutorHandoff, concept_id: str) -> str:
    """Deterministic question_id, so a replayed handoff re-persists the
    same question instead of minting a second one for the same check
    (message-flow.md: "Replayed proposals must not append another
    attempt"; the same reasoning applies to the question itself)."""

    return str(uuid5(NAMESPACE_URL, f"netra-question:{handoff.handoff_id}:{concept_id}"))


# --- prompt assembly -------------------------------------------------------


def _format_evidence(evidence: Sequence[Evidence]) -> str:
    return "\n\n".join(
        f"[{item.evidence_id}] ({item.locator}) {item.text[:MAX_EVIDENCE_EXCERPT_CHARS]}"
        for item in evidence
    )


def _format_dialogue(handoff: CoordinatorToTutorHandoff) -> str:
    """Bounded relevant dialogue only — the contract already caps it at 8
    turns, and the Coordinator's own history never crosses this boundary."""

    return "\n".join(f"{turn.role}: {turn.content}" for turn in handoff.recent_dialogue)


def _build_explanation_prompt(
    handoff: CoordinatorToTutorHandoff,
    instruction: str,
    evidence: Sequence[Evidence],
    history: RelevantHistory,
) -> str:
    return "\n\n".join(
        part
        for part in (
            instruction,
            f"Learning goal: {handoff.learning_goal}",
            f"Explanation level: {handoff.explanation_level}",
            f"Evidence:\n{_format_evidence(evidence)}",
            _format_history(history),
            f"Recent dialogue:\n{_format_dialogue(handoff)}" if handoff.recent_dialogue else "",
            f"The student said: {handoff.original_utterance}",
        )
        if part
    )


def _build_hint_prompt(
    handoff: CoordinatorToTutorHandoff,
    instruction: str,
    evidence: Sequence[Evidence],
    question_prompt: str,
    hints_already_given: int,
) -> str:
    """Assemble the hint prompt.

    Takes the question's public prompt text, not the ApprovedQuestion:
    the answer key and rubric are never in scope for a hint, so there is
    no path by which this prompt can contain them.
    """

    return "\n\n".join(
        part
        for part in (
            instruction,
            f"Evidence:\n{_format_evidence(evidence)}",
            f"The student is working on this question: {question_prompt}",
            f"Hints already given for it: {hints_already_given}",
            f"Recent dialogue:\n{_format_dialogue(handoff)}" if handoff.recent_dialogue else "",
            f"The student said: {handoff.original_utterance}",
            "Give one hint that moves them forward using the evidence. "
            "Do not state the answer, and do not ask a new question.",
        )
        if part
    )


def _build_grading_prompt(
    handoff: CoordinatorToTutorHandoff,
    instruction: str,
    evidence: Sequence[Evidence],
    question: ApprovedQuestion,
    answer_text: str,
) -> str:
    """Assemble the rubric-grading prompt.

    The rubric and reference answer are server-side grading material. They
    are supplied to the model because rubric grading is the Tutor's job
    (agent-boundaries.md), and the instruction forbids restating them to
    the student; they never reach a PublicSegment through any path in this
    module. That instruction is the only control on the model echoing them
    back inside its feedback — a residual risk recorded in
    docs/team/handoffs/M4.md rather than one this assembly can close.
    """

    return "\n\n".join(
        part
        for part in (
            instruction,
            f"Evidence:\n{_format_evidence(evidence)}",
            f"Question asked: {question.prompt}",
            f"Reference answer (do not reveal): {question.answer_key.correct_answer or 'n/a'}",
            f"Grading rubric (do not reveal): {question.answer_key.rubric or 'n/a'}",
            f"Recent dialogue:\n{_format_dialogue(handoff)}" if handoff.recent_dialogue else "",
            f"The student's answer: {answer_text}",
            "Evaluate the student's answer as instructed.",
        )
        if part
    )
