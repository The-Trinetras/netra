"""Bounded Coordinator turn: choose -> act -> check result -> repair, answer, delegate or stop.

AgentSpec state machine (section 6) implemented as an explicit loop with
application-enforced stops:

    check access/load context -> choose next action (model decision)
      -> execute bounded action (tool gateway | tutor handoff)
      -> check result (evidence ledger)
         -> repair can help: choose again with the specific failed check
         -> answer supported: finish with validated, cited public text
         -> student input needed: finish with clarification / saved question
         -> no progress or limit reached: finish with supported findings + stated gaps

Invariants enforced here, not by prompts:
- Deterministic commands never reach this loop (coordinator.router.route_turn
  and the dispatcher run first).
- Every model decision is counted BEFORE the call, including invalid outputs,
  provider failures retried and fallback, against the one shared TurnBudget
  (4 decisions / 6 tools / 20 s). Tutor delegation spends the same instance.
- Cancellation (STOP, supersession, disconnect) ends the turn with no output
  at all; nothing produced after cancellation is returned.
- A sufficient first result proceeds directly: no repair is forced.
- An answer or delegation is accepted only when every declared requirement is
  application-verified as supported and every cited id is validated evidence.
- An identical action that already ran while requirements remain unresolved is
  refused and the turn ends with the stated gap (no-progress stop, not a cap).

LangGraph (approved stack) is not installed in the verified environment, so
the loop runs as plain asyncio. ``build_langgraph`` wires the same engine as a
single bounded node when the pinned package is present; that wiring and
PostgreSQL checkpointing are unverified here and recorded in the M1 handoff.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from netra_api.coordinator.context import ContextSelector, load_coordinator_instructions, render_prompt
from netra_api.coordinator.decisions import InvalidDecisionError, ParsedDecision, parse_decision
from netra_api.coordinator.evidence_check import EvidenceLedger
from netra_api.coordinator.limits import TurnBudget
from netra_api.coordinator.providers.gemini import (
    DEFAULT_COORDINATOR_MODEL_CONFIG,
    GeminiCoordinatorProvider,
    GeminiModelConfig,
    ModelDecision,
    ToolSpec,
)
from netra_api.coordinator.state import CoordinatorTurnState, TurnOutcome
from netra_api.coordinator.tool_registry import ToolContext, ToolGateway, ToolRegistry
from netra_api.coordinator.tutor_gateway import HandoffRejectedError, TutorGateway
from netra_api.platform.errors import NetraError, TurnCancelledError
from netra_api.platform.observability import TurnTrace
from netra_api.platform.tracing import DISABLED_TRACER, Tracer
from netra_api.session.outputs import PlannedSegment
from netra_api.session.state import PendingQuestionRef

logger = logging.getLogger(__name__)

LIMIT_PREFIX = "I stopped before finishing this answer."
UNAVAILABLE_TEXT = "I can't answer that right now because a required service is unavailable."


class _ModelCallFailed(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason


class CoordinatorEngine:
    def __init__(
        self,
        *,
        model: GeminiCoordinatorProvider,
        registry: ToolRegistry,
        context: ContextSelector,
        tutor: Optional[TutorGateway] = None,
        model_config: GeminiModelConfig = DEFAULT_COORDINATOR_MODEL_CONFIG,
        instructions: Optional[str] = None,
        tracer: Tracer = DISABLED_TRACER,
    ) -> None:
        self._model = model
        self._registry = registry
        self._tracer = tracer
        self._gateway = ToolGateway(registry, tracer=tracer)
        self._context = context
        self._tutor = tutor
        self._model_config = model_config
        self._instructions = instructions if instructions is not None else load_coordinator_instructions()

    async def run(self, turn: CoordinatorTurnState, trace: TurnTrace) -> TurnOutcome:
        with self._tracer.span("netra.coordinator.turn", netra_request_id=str(turn.request_id), netra_operation="coordinator_turn") as span:
            outcome = await self._run(turn, trace)
            budget = turn.budget
            span.set(
                netra_outcome=outcome.kind,
                netra_budget_model_decisions_used=budget.model_decisions_used,
                netra_budget_tool_calls_used=budget.tool_calls_used,
                netra_budget_nested_model_calls=budget.nested_model_calls,
            )
            if outcome.kind == "cancelled":
                span.fail("cancelled")
            return outcome

    async def _run(self, turn: CoordinatorTurnState, trace: TurnTrace) -> TurnOutcome:
        if turn.session is None:
            raise ValueError("a Coordinator turn requires the accepted session state")
        budget = turn.budget
        ledger = EvidenceLedger()
        feedback: list[str] = []
        had_gap = False
        trace.record("turn_started", mode=turn.session.interaction_mode.value)
        selected = await self._context.select(turn.session, trace)

        while True:
            if budget.cancelled:
                return self._cancelled(trace)
            if budget.is_expired() or budget.remaining_model_decisions == 0:
                return self._limited(ledger, trace, "deadline" if budget.is_expired() else "model_decisions")

            tools = self._registry.permitted(turn.session)
            prompt = render_prompt(
                instructions=self._instructions,
                utterance=turn.original_utterance,
                selected=selected,
                ledger=ledger,
                feedback=feedback,
                tools=tools,
                can_delegate=self._tutor is not None,
                remaining_model_decisions=budget.remaining_model_decisions,
                remaining_tool_calls=budget.remaining_tool_calls,
            )
            specs = [ToolSpec(name=t.name, description=t.description, input_schema=t.input_schema()) for t in tools]

            budget.register_model_decision()
            trace.record("model_decision", attempt=budget.model_decisions_used, tools_offered=[t.name for t in tools])
            with self._tracer.span(
                "netra.model.decision",
                netra_operation="model_decision",
                llm_provider="google",
                llm_model_name=self._model_config.model_id,
                netra_attempt=budget.model_decisions_used,
            ) as model_span:
                try:
                    decision = await self._decide(prompt, specs, budget)
                except TurnCancelledError:
                    model_span.fail("cancelled")
                    return self._cancelled(trace)
                except _ModelCallFailed as failure:
                    model_span.fail("timeout" if failure.reason == "deadline_reached" else "error", failure.reason.lower())
                    feedback.append(f"model call failed: {failure.reason}")
                    trace.record("model_output_rejected", check=failure.reason)
                    continue
            if budget.cancelled:
                return self._cancelled(trace)

            try:
                parsed = parse_decision(decision)
            except InvalidDecisionError as invalid:
                feedback.append(f"previous output rejected: {invalid.check}")
                trace.record("model_output_rejected", check=invalid.check)
                continue

            feedback = []
            payload = parsed.payload
            ledger.add_requirements(payload.requirements)
            for gap in ledger.apply_assessments(payload.assessments):
                had_gap = True
                trace.record(
                    "evidence_gap",
                    requirement=gap.requirement_id,
                    status=gap.status,
                    gap=gap.gap_code,
                    evidence_id=gap.evidence_id,
                )
                feedback.append(f"requirement {gap.requirement_id} is {gap.status} ({gap.gap_code})")

            if payload.action == "call_tools":
                outcome = await self._call_tools(turn, parsed, ledger, trace, feedback, had_gap)
                if outcome is not None:
                    return outcome
                continue

            if payload.action in ("clarify", "state_gap"):
                kind = "clarification" if payload.action == "clarify" else "gap"
                segment_kind = "question" if payload.action == "clarify" else "explanation"
                trace.record("turn_completed", outcome=kind, open_requirements=[s.requirement.requirement_id for s in ledger.open_requirements()])
                return TurnOutcome(kind=kind, segments=(PlannedSegment(origin="generated", kind=segment_kind, text=payload.text),))

            problems = self._final_problems(ledger, payload.cited_evidence_ids if payload.action == "answer" else payload.tutor.evidence_ids)
            if payload.action == "delegate_to_tutor" and self._tutor is None:
                problems.append("tutor_delegation_unavailable")
            if problems:
                trace.record("model_output_rejected", check=problems)
                feedback.extend(problems)
                continue

            supported = ledger.supported_requirements()
            trace.record(
                "evidence_validated",
                requirements=[s.requirement.requirement_id for s in supported],
                approximate=[s.requirement.requirement_id for s in supported if s.approximate],
                evidence_ids=list(payload.cited_evidence_ids or payload.tutor.evidence_ids),
            )

            if payload.action == "answer":
                trace.record("turn_completed", outcome="answer")
                return TurnOutcome(
                    kind="answer",
                    segments=(
                        PlannedSegment(
                            origin="generated", kind="explanation", text=payload.text, evidence_ids=payload.cited_evidence_ids
                        ),
                    ),
                )

            with self._tracer.span("netra.tutor.handoff", netra_operation="tutor_handoff", netra_handoff_mode=payload.tutor.mode) as handoff_span:
                outcome = await self._delegate(turn, ledger, parsed, trace, feedback)
                handoff_span.set(netra_outcome=outcome.kind if outcome is not None else "retry_with_feedback")
                if outcome is not None and outcome.kind == "cancelled":
                    handoff_span.fail("cancelled")
            if outcome is not None:
                return outcome

    async def _decide(self, prompt: str, specs: list[ToolSpec], budget: TurnBudget) -> ModelDecision:
        call = asyncio.ensure_future(self._model.decide(self._model_config, prompt, specs))
        cancel_waiter = asyncio.ensure_future(budget.wait_cancelled())
        try:
            done, _ = await asyncio.wait(
                {call, cancel_waiter}, timeout=budget.remaining_seconds(), return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            cancel_waiter.cancel()
        if call not in done:
            call.cancel()
            await asyncio.gather(call, return_exceptions=True)
            if budget.cancelled:
                raise TurnCancelledError("cancelled during model decision")
            raise _ModelCallFailed("deadline_reached")
        try:
            decision = call.result()
        except NetraError as exc:
            raise _ModelCallFailed(type(exc).__name__) from exc
        except Exception as exc:  # provider boundary: never leak provider detail
            logger.warning("coordinator model failed: %s", type(exc).__name__)
            raise _ModelCallFailed("provider_error") from exc
        if not isinstance(decision, ModelDecision):
            try:
                decision = ModelDecision.model_validate(decision)
            except Exception as exc:
                raise _ModelCallFailed("provider_output_invalid") from exc
        return decision

    async def _call_tools(self, turn, parsed: ParsedDecision, ledger: EvidenceLedger, trace, feedback, had_gap) -> Optional[TurnOutcome]:
        calls = []
        for request in parsed.tool_calls:
            key = ToolGateway.action_key(request)
            if ledger.is_unproductive_repeat(key):
                trace.record("repetition_blocked", tool=request.tool_name)
                continue
            calls.append((key, request))
        if not calls:
            return self._stated_gap(ledger, trace, "repetition")

        if had_gap:
            trace.record(
                "action_changed",
                reason_requirements=[s.requirement.requirement_id for s in ledger.open_requirements()],
                reason_gaps=[s.gap_code for s in ledger.open_requirements()],
                new_tools=[request.tool_name for _, request in calls],
            )

        context = ToolContext(
            auth=turn.auth,
            budget=turn.budget,
            session=turn.session,
            trace=trace,
            companion_source_version_ids=turn.companion_source_version_ids,
        )
        outcomes = await self._gateway.dispatch(context, [request for _, request in calls])
        if turn.budget.cancelled:
            return self._cancelled(trace)

        for (key, request), outcome in zip(calls, outcomes):
            if outcome.status in ("ok", "timeout", "failed"):
                ledger.executed_actions.add(key)
            if outcome.status != "ok" or outcome.result is None:
                trace.record("tool_result", tool=request.tool_name, status=outcome.status, reason=outcome.reason)
                feedback.append(f"tool {request.tool_name} {outcome.status}: {outcome.reason}")
                continue
            new_ids = ledger.add_evidence(outcome.result.evidence)
            ledger.rejected_evidence_count += outcome.result.rejected_count
            trace.record(
                "tool_result",
                tool=request.tool_name,
                status="ok",
                evidence_ids=[e.evidence_id for e in outcome.result.evidence],
                new_evidence=len(new_ids),
            )
            if outcome.result.rejected_count:
                trace.record("evidence_rejected", tool=request.tool_name, count=outcome.result.rejected_count)
            if not outcome.result.evidence:
                feedback.append(f"tool {request.tool_name} returned no authorized evidence")
        return None

    async def _delegate(self, turn, ledger, parsed: ParsedDecision, trace, feedback) -> Optional[TurnOutcome]:
        request = parsed.payload.tutor
        try:
            delegation = await self._tutor.delegate(
                auth=turn.auth,
                session=turn.session,
                request_id=turn.request_id,
                utterance=turn.original_utterance,
                budget=turn.budget,
                ledger=ledger,
                request=request,
                trace=trace,
            )
        except TurnCancelledError:
            return self._cancelled(trace)
        except HandoffRejectedError as rejected:
            trace.record("handoff_rejected", check=rejected.check)
            if rejected.check in ("evidence_version_unavailable", "handoff_evidence_not_validated"):
                feedback.append(f"delegation refused: {rejected.check}")
                return None
            return self._failed(trace, rejected.check)
        except NetraError as exc:
            trace.record("handoff_rejected", check=type(exc).__name__)
            if turn.budget.is_expired():
                return self._limited(ledger, trace, "deadline")
            return self._failed(trace, type(exc).__name__)

        if turn.budget.cancelled:
            return self._cancelled(trace)

        result = delegation.result
        if result.status == "needs_more_evidence":
            feedback.append("tutor needs more evidence for this goal")
            return None
        if result.status == "failed":
            return self._failed(trace, "tutor_failed")

        segments = tuple(
            PlannedSegment(origin="generated", kind=segment.kind, text=segment.text, evidence_ids=tuple(result.evidence_ids))
            for segment in result.public_segments
            if segment.text
        )
        hints = sum(1 for segment in result.public_segments if segment.kind == "hint")
        pending = turn.session.pending_question
        new_pending = None
        question = delegation.question
        if question is not None:
            carried = pending.hints_used if pending and pending.question_id == question.question_id else 0
            new_pending = PendingQuestionRef(
                question_id=question.question_id, question_version=question.question_version, hints_used=carried + hints
            )
            question = question.model_copy(update={"hints_used": new_pending.hints_used})
        elif pending is not None and hints:
            new_pending = pending.model_copy(update={"hints_used": pending.hints_used + hints})

        clear = delegation.handoff.mode == "evaluate_answer" and result.status == "completed" and question is None and not hints
        trace.record("turn_completed", outcome="tutor", status=result.status)
        return TurnOutcome(
            kind="tutor",
            segments=segments,
            question=question,
            lesson_id=delegation.handoff.lesson_id,
            pending_question=new_pending,
            clear_pending_question=clear,
            reply_role="tutor",
        )

    @staticmethod
    def _final_problems(ledger: EvidenceLedger, cited: tuple[str, ...]) -> list[str]:
        problems = []
        if not cited:
            problems.append("no_validated_evidence_cited")
        unvalidated = ledger.unvalidated(cited)
        if unvalidated:
            problems.append("cited_evidence_not_validated")
        open_requirements = ledger.open_requirements()
        if open_requirements:
            problems.append("requirements_unresolved:" + ",".join(s.requirement.requirement_id for s in open_requirements))
        return problems

    @staticmethod
    def _gap_sentence(ledger: EvidenceLedger) -> str:
        parts = []
        for state in ledger.open_requirements():
            reason = "it is unreadable in the source" if state.status == "unreadable" else "the available material does not show it"
            parts.append(f"{state.requirement.description} ({reason})")
        if not parts:
            return "I could not find authorized evidence that answers this."
        return "I could not establish: " + "; ".join(parts) + ". I won't guess."

    def _stated_gap(self, ledger: EvidenceLedger, trace: TurnTrace, reason: str) -> TurnOutcome:
        text = self._gap_sentence(ledger) + " You can point me to another part of the material or rephrase."
        trace.record("turn_completed", outcome="gap", reason=reason)
        return TurnOutcome(kind="gap", segments=(PlannedSegment(origin="generated", kind="explanation", text=text),))

    def _limited(self, ledger: EvidenceLedger, trace: TurnTrace, limit: str) -> TurnOutcome:
        trace.record("budget_exhausted", limit=limit)
        supported = ledger.supported_requirements()
        text = LIMIT_PREFIX
        if supported:
            text += " Supported by the material: " + "; ".join(
                f"{s.requirement.description} (evidence {s.evidence_id}{', approximate' if s.approximate else ''})"
                for s in supported
            ) + "."
        if ledger.open_requirements() or not supported:
            text += " " + self._gap_sentence(ledger)
        return TurnOutcome(kind="limited", segments=(PlannedSegment(origin="generated", kind="explanation", text=text),))

    @staticmethod
    def _cancelled(trace: TurnTrace) -> TurnOutcome:
        trace.record("turn_cancelled")
        return TurnOutcome(kind="cancelled")

    @staticmethod
    def _failed(trace: TurnTrace, reason: str) -> TurnOutcome:
        trace.record("turn_completed", outcome="failed", reason=reason)
        return TurnOutcome(kind="failed", segments=(PlannedSegment(origin="generated", kind="explanation", text=UNAVAILABLE_TEXT),))


def build_langgraph(engine: CoordinatorEngine) -> Any:
    """Compile the engine as a one-node LangGraph graph (langgraph==1.2.11).

    Unverified in this environment (package not installed). The budget,
    cancellation and validation stay inside the engine, so graph retries or
    checkpoint resumes cannot reset counters: the TurnBudget instance travels
    in state, and a resumed graph must be handed the ORIGINAL instance.
    """

    from typing import TypedDict

    from langgraph.graph import END, START, StateGraph  # type: ignore[import-not-found]

    class _GraphState(TypedDict, total=False):
        turn: CoordinatorTurnState
        trace: TurnTrace
        outcome: TurnOutcome

    async def run_node(state: _GraphState) -> dict:
        return {"outcome": await engine.run(state["turn"], state["trace"])}

    graph = StateGraph(_GraphState)
    graph.add_node("coordinator_turn", run_node)
    graph.add_edge(START, "coordinator_turn")
    graph.add_edge("coordinator_turn", END)
    return graph.compile()
