"""A turn asked with no source open.

Every tool that reads the student's material requires a pinned source, so such
a turn used to spend its whole budget discovering that and then ask which
material the student meant - a question typing cannot answer, because opening
a source is what unblocks it. It now says so deterministically, before any
model call.
"""

import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from netra_api.coordinator.context import ContextSelector
from netra_api.coordinator.graph import NO_SOURCE_OPEN, CoordinatorEngine
from netra_api.coordinator.limits import TurnBudget
from netra_api.coordinator.providers.gemini import ModelDecision
from netra_api.coordinator.state import CoordinatorTurnState
from netra_api.coordinator.tool_registry import ToolDefinition, ToolRegistry
from netra_api.platform.auth_context import AuthContext
from netra_api.platform.observability import InMemoryTraceSink, TurnTrace
from netra_api.session.state import (
    AccountContext, ConnectionState, InteractionMode, ReadingPosition, SessionState)
from pydantic import BaseModel


class Args(BaseModel):
    query: str


class RecordingModel:
    """Records calls and clarifies, so a turn that is NOT short-circuited ends
    in an ordinary clarification with the model's own words."""

    provider_name = "test"
    model_name = "test-model"

    def __init__(self) -> None:
        self.calls = 0

    async def decide(self, *args, **kwargs):
        self.calls += 1
        return ModelDecision(
            raw_text=json.dumps({"action": "clarify", "text": "Which chapter?", "assessments": []}),
            finish_reason="stop")


async def _tool(context, arguments):
    raise AssertionError("no tool may run")


def _session(pinned):
    session_id = uuid4()
    return SessionState(
        session_id=session_id,
        account=AccountContext(account_id=uuid4()),
        connection_state=ConnectionState.CONNECTED,
        interaction_mode=InteractionMode.READING,
        reading_position=ReadingPosition(source_version_id=pinned),
        updated_at=datetime.now(timezone.utc),
    )


def _engine(*, requires_pinned_source=True, model=None):
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(name="search_sources", description="d", input_model=Args, timeout_seconds=5.0,
                       requires_pinned_source=requires_pinned_source),
        _tool)
    return CoordinatorEngine(model=model or RecordingModel(), registry=registry, context=ContextSelector(None))


def _turn(session):
    auth = AuthContext(account_id=session.account.account_id, session_id=session.session_id,
                       request_id=uuid4(), issued_at=datetime.now(timezone.utc))
    return CoordinatorTurnState(session_id=session.session_id, auth=auth, request_id=auth.request_id,
                                original_utterance="what is a kernel?",
                                session=session, budget=TurnBudget())


def _trace(turn):
    sink = InMemoryTraceSink()
    return TurnTrace(sink, request_id=turn.request_id, session_id=turn.session.session_id), sink


@pytest.mark.asyncio
async def test_with_no_source_open_it_says_what_unblocks_the_question():
    turn = _turn(_session(pinned=None))
    trace, sink = _trace(turn)

    outcome = await _engine()._run(turn, trace)

    assert outcome.kind == "clarification"
    assert outcome.segments[0].text == NO_SOURCE_OPEN
    assert "library" in NO_SOURCE_OPEN


@pytest.mark.asyncio
async def test_it_costs_no_model_decision_and_no_tool_call():
    turn = _turn(_session(pinned=None))
    trace, _ = _trace(turn)

    await _engine()._run(turn, trace)

    # Nothing was spent: no decision counted, no tool called.
    assert turn.budget.model_decisions_used == 0
    assert turn.budget.tool_calls_used == 0


@pytest.mark.asyncio
async def test_an_open_source_still_runs_the_normal_turn():
    turn = _turn(_session(pinned=str(uuid4())))
    trace, _ = _trace(turn)
    model = RecordingModel()

    outcome = await _engine(model=model)._run(turn, trace)

    # Reaching the model, and answering in its words, proves no short-circuit.
    assert model.calls == 1
    assert outcome.segments[0].text == "Which chapter?"


@pytest.mark.asyncio
async def test_it_does_not_fire_when_no_tool_needs_a_pinned_source():
    turn = _turn(_session(pinned=None))
    trace, _ = _trace(turn)
    model = RecordingModel()

    outcome = await _engine(requires_pinned_source=False, model=model)._run(turn, trace)

    assert model.calls == 1
    assert outcome.segments[0].text != NO_SOURCE_OPEN
