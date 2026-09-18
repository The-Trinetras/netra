"""Execute the pinned LangGraph integration (langgraph==1.2.11) with the real engine.

The compiled graph must be a transparent orchestrator: identical outcome and
evidence repair to a direct engine run, the originating TurnBudget instance
carried through (never a fresh one), and STOP cancellation still ending the
turn promptly. The Coordinator model is the scripted double; everything else
is the real engine, tool gateway and fixture services.
"""

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from netra_api.coordinator.context import ContextSelector
from netra_api.coordinator.graph import CoordinatorEngine, build_langgraph
from netra_api.coordinator.limits import TurnBudget
from netra_api.coordinator.state import CoordinatorTurnState
from netra_api.coordinator.tool_registry import ToolRegistry
from netra_api.coordinator.tools import register_available_tools
from netra_api.platform.auth_context import AuthContext
from netra_api.platform.observability import InMemoryTraceSink, TurnTrace

from ohm_fixture import (
    ACCOUNT,
    AXES,
    LECTURE_V1,
    SESSION,
    FixtureFigures,
    FixtureResolver,
    FixtureVideo,
    ScriptedModel,
    final,
    initial_state,
    tools,
)


def _script():
    return [
        tools(("search_lecture", {"query": "this line", "lecture_source_version_id": str(LECTURE_V1),
                                  "start_ms": 42000, "end_ms": 58000}), requirements=AXES),
        tools(("describe_figure", {"figure_index": 1}), assessments=[
            {"requirement_id": "x-axis", "status": "missing", "evidence_id": "ev-lecture-0042", "gap": "axes not in transcript"},
            {"requirement_id": "y-axis", "status": "missing", "evidence_id": "ev-lecture-0042", "gap": "axes not in transcript"}]),
        final({"action": "answer", "assessments": [
            {"requirement_id": "x-axis", "status": "supported", "evidence_id": "ev-fig02", "observation_label": "x-axis: current (A)"},
            {"requirement_id": "y-axis", "status": "supported", "evidence_id": "ev-fig02", "observation_label": "y-axis: voltage (V)"}],
            "text": "Figure 2 plots current on x and voltage on y; the straight line means a constant ratio.",
            "cited_evidence_ids": ["ev-fig02", "ev-lecture-0042"]}),
    ]


def _engine(model):
    registry = ToolRegistry()
    register_available_tools(registry, resolver=FixtureResolver(), figures=FixtureFigures(), video=FixtureVideo())
    return CoordinatorEngine(model=model, registry=registry, context=ContextSelector(None))


def _turn(budget):
    auth = AuthContext(account_id=ACCOUNT, session_id=SESSION, request_id=uuid4(), issued_at=datetime.now(timezone.utc))
    return CoordinatorTurnState(session_id=SESSION, request_id=auth.request_id, auth=auth, budget=budget,
                                original_utterance="How does this line show constant resistance?", session=initial_state(),
                                companion_source_version_ids=frozenset({str(LECTURE_V1)}))


async def test_the_compiled_graph_runs_the_same_bounded_turn_as_the_engine():
    direct_budget, graph_budget = TurnBudget(), TurnBudget()
    direct = await _engine(ScriptedModel(_script())).run(
        _turn(direct_budget), TurnTrace(InMemoryTraceSink(), request_id=uuid4(), session_id=SESSION))

    sink = InMemoryTraceSink()
    turn = _turn(graph_budget)
    graph = build_langgraph(_engine(ScriptedModel(_script())))
    state = await graph.ainvoke({"turn": turn, "trace": TurnTrace(sink, request_id=turn.request_id, session_id=SESSION)})

    outcome = state["outcome"]
    assert outcome.kind == direct.kind == "answer"
    assert [s.text for s in outcome.segments] == [s.text for s in direct.segments]
    assert state["turn"].budget is graph_budget  # the originating instance, not a copy
    assert (graph_budget.model_decisions_used, graph_budget.tool_calls_used) == (
        direct_budget.model_decisions_used, direct_budget.tool_calls_used) == (3, 2)
    assert sink.of_kind("evidence_gap") and sink.of_kind("action_changed")


async def test_stop_cancels_a_turn_running_inside_the_graph():
    budget = TurnBudget()
    turn = _turn(budget)
    graph = build_langgraph(_engine(ScriptedModel(_script(), delay=5.0)))
    running = asyncio.ensure_future(graph.ainvoke(
        {"turn": turn, "trace": TurnTrace(InMemoryTraceSink(), request_id=turn.request_id, session_id=SESSION)}))
    await asyncio.sleep(0.1)
    started = asyncio.get_running_loop().time()
    budget.cancel()
    state = await asyncio.wait_for(running, 2.0)
    assert state["outcome"].kind == "cancelled"
    assert asyncio.get_running_loop().time() - started < 1.0
