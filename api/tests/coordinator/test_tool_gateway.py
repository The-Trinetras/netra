"""Tool gateway: permitted subset, strict arguments, atomic shared budget, timeouts, cancellation."""

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from netra_api.content.retrieval.evidence import EvidenceTrust
from netra_api.coordinator.limits import MAX_TOOL_CALLS_PER_TURN, TurnBudget
from netra_api.coordinator.providers.gemini import ToolCallRequest
from netra_api.coordinator.tool_registry import (
    ToolContext,
    ToolDefinition,
    ToolEvidence,
    ToolGateway,
    ToolRegistry,
    ToolResult,
)
from netra_api.platform.auth_context import AuthContext
from netra_api.platform.observability import InMemoryTraceSink, TurnTrace
from netra_api.session.modes import ConnectionState, InteractionMode
from netra_api.session.state import AccountContext, ReadingPosition, SessionState

PINNED = str(uuid4())


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str


class RecordingTool:
    def __init__(self, delay=0.0, source=PINNED):
        self.delay = delay
        self.source = source
        self.started = 0
        self.max_concurrent = 0
        self._running = 0

    async def __call__(self, context, arguments):
        self.started += 1
        self._running += 1
        self.max_concurrent = max(self.max_concurrent, self._running)
        try:
            await asyncio.sleep(self.delay)
        finally:
            self._running -= 1
        return ToolResult(
            evidence=(
                ToolEvidence(evidence_id=f"ev-{arguments.query}", source_version_id=self.source, locator="p1", text="t", provenance="test", trust=EvidenceTrust.SOURCE_VERIFIED),
            )
        )


def _context(budget=None, mode=InteractionMode.READING, pinned=PINNED):
    session_id = uuid4()
    auth = AuthContext(account_id=uuid4(), session_id=session_id, request_id=uuid4(), issued_at=datetime.now(timezone.utc))
    state = SessionState(
        session_id=session_id,
        account=AccountContext(account_id=auth.account_id),
        connection_state=ConnectionState.CONNECTED,
        interaction_mode=mode,
        reading_position=ReadingPosition(source_version_id=pinned),
        updated_at=datetime.now(timezone.utc),
    )
    sink = InMemoryTraceSink()
    return ToolContext(auth=auth, budget=budget or TurnBudget(), session=state, trace=TurnTrace(sink, request_id=auth.request_id, session_id=session_id)), sink


def _gateway(tool, **definition):
    registry = ToolRegistry()
    registry.register(ToolDefinition(name="search", description="d", input_model=Args, timeout_seconds=definition.pop("timeout", 5.0), **definition), tool)
    return ToolGateway(registry), registry


def _call(query="q", **extra):
    return ToolCallRequest(tool_name="search", arguments={"query": query, **extra})


async def test_parallel_batch_cannot_overshoot_the_shared_tool_budget():
    tool = RecordingTool(delay=0.01)
    gateway, _ = _gateway(tool)
    budget = TurnBudget()
    budget.register_tool_call()
    budget.register_tool_call()
    context, _ = _context(budget)

    outcomes = await gateway.dispatch(context, [_call(str(i)) for i in range(6)])

    assert [o.status for o in outcomes].count("ok") == MAX_TOOL_CALLS_PER_TURN - 2
    assert [o.status for o in outcomes].count("not_dispatched") == 2
    assert budget.tool_calls_used == MAX_TOOL_CALLS_PER_TURN
    assert tool.started == 4 and tool.max_concurrent == 4


async def test_arguments_carrying_authority_fields_are_rejected_and_cost_nothing():
    tool = RecordingTool()
    gateway, _ = _gateway(tool)
    context, sink = _context()
    outcomes = await gateway.dispatch(context, [_call(account_id=str(uuid4())), ToolCallRequest(tool_name="shell", arguments={})])
    assert [o.reason for o in outcomes] == ["invalid_arguments", "tool_not_permitted"]
    assert context.budget.tool_calls_used == 0 and tool.started == 0


async def test_tool_not_permitted_in_current_mode_is_not_offered_or_run():
    tool = RecordingTool()
    gateway, registry = _gateway(tool, allowed_modes=frozenset({InteractionMode.READING}))
    context, _ = _context(mode=InteractionMode.QUIZ)
    assert registry.permitted(context.session) == []
    outcomes = await gateway.dispatch(context, [_call()])
    assert outcomes[0].reason == "tool_not_permitted"


async def test_timeout_is_capped_by_remaining_turn_time():
    tool = RecordingTool(delay=5.0)
    gateway, _ = _gateway(tool, timeout=30.0)
    budget = TurnBudget(started_at=datetime.now(timezone.utc) - timedelta(seconds=19.9))
    context, sink = _context(budget)
    started = asyncio.get_running_loop().time()
    outcomes = await gateway.dispatch(context, [_call()])
    assert outcomes[0].status == "timeout"
    assert asyncio.get_running_loop().time() - started < 1.0
    assert sink.of_kind("tool_dispatched")[0].detail["timeout_s"] <= 0.1


async def test_cancellation_during_tool_abandons_the_call_immediately():
    tool = RecordingTool(delay=5.0)
    gateway, _ = _gateway(tool)
    context, _ = _context()
    dispatch = asyncio.ensure_future(gateway.dispatch(context, [_call("a"), _call("b")]))
    await asyncio.sleep(0.02)
    context.budget.cancel()
    outcomes = await asyncio.wait_for(dispatch, 1.0)
    assert {o.status for o in outcomes} == {"cancelled"}


async def test_result_from_an_unpinned_source_version_is_refused():
    tool = RecordingTool(source=str(uuid4()))
    gateway, _ = _gateway(tool)
    context, _ = _context()
    outcomes = await gateway.dispatch(context, [_call()])
    assert outcomes[0].status == "failed" and outcomes[0].reason == "invalid_tool_result"


async def test_nested_model_calls_are_recorded_but_not_counted_as_decisions():
    context, sink = _context()
    context.record_nested_model_call("video_description")
    assert context.budget.nested_model_calls == 1
    assert context.budget.model_decisions_used == 0
    assert sink.of_kind("nested_model_call")[0].detail == {"operation": "video_description", "total": 1}


def test_retransmission_budget_keeps_counters_and_deadline():
    budget = TurnBudget()
    budget.register_model_decision()
    budget.register_tool_call()
    budget.cancel()
    retry = budget.for_retransmission()
    assert (retry.model_decisions_used, retry.tool_calls_used, retry.deadline_at) == (1, 1, budget.deadline_at)
    assert retry.cancelled is False
