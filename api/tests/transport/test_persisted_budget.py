"""D-BUDGET: turn budget use persisted per request id survives an API restart.

LABELLED FIXTURE RUN: scripted Coordinator model, in-memory ledger standing
in for PostgreSQL (test_budget_ledger_postgres.py runs the real table). A
"restart" is a fresh TurnRegistry: the in-process record of the turn is gone.
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from netra_api.coordinator.budget_ledger import BudgetUsage, InMemoryBudgetLedger
from netra_api.coordinator.limits import MAX_MODEL_DECISIONS_PER_TURN, TurnBudget
from netra_api.platform.errors import IdempotencyConflictError
from netra_api.transport.websocket.dispatcher import TurnRegistry
from netra_api.transport.websocket.endpoint import serve

from ohm_fixture import ACCOUNT, SESSION, FakeSocket, ScriptedModel, build_journey, envelope, final, wait_for

NOW = datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc)


async def _open(journey):
    socket = FakeSocket()
    task = asyncio.ensure_future(serve(socket, journey.services, journey.composition.verifier))
    await wait_for(lambda: socket.accepted)
    return socket, task


async def _close(socket, task):
    socket.disconnect()
    await asyncio.wait_for(task, 2)


def _turn(request_id):
    return envelope(
        "turn.submit",
        {"utterance": "Explain the table.", "input_mode": "voice", "transcript_status": "final", "expected_session_version": 10},
        request_id=request_id,
    )


async def _submit(socket, message, timeout=5.0):
    socket.push(message)
    rid = message["request_id"]
    await wait_for(lambda: any(m["request_id"] == rid and m["type"] in ("response.segment", "error") for m in socket.texts), timeout)
    return [m for m in socket.texts if m["request_id"] == rid]


async def _drop_mid_turn(journey, model, turn_id):
    socket, task = await _open(journey)
    socket.push(_turn(turn_id))
    await wait_for(lambda: model.calls == 1)
    await _close(socket, task)


async def test_retransmission_after_restart_resumes_the_spent_budget():
    model = ScriptedModel([final({"action": "answer"})] * 10, delay=0.2)
    journey = await build_journey(model=model)
    ledger = journey.services.budgets = InMemoryBudgetLedger()
    turn_id = uuid4()
    await _drop_mid_turn(journey, model, turn_id)
    assert ledger.rows[(ACCOUNT, turn_id)][1].model_decisions_used == 1

    journey.services.turns = TurnRegistry()  # restart: the in-process turn is gone
    socket, task = await _open(journey)
    responses = await _submit(socket, _turn(turn_id))
    assert "I stopped before finishing" in json.dumps(responses)
    assert model.calls == MAX_MODEL_DECISIONS_PER_TURN  # 1 before the restart + 3 after, not 1 + 4
    await _close(socket, task)


async def test_without_the_ledger_a_restart_would_grant_a_fresh_budget():
    """Control: the failure D-BUDGET fixes."""

    model = ScriptedModel([final({"action": "answer"})] * 10, delay=0.2)
    journey = await build_journey(model=model)
    turn_id = uuid4()
    await _drop_mid_turn(journey, model, turn_id)
    journey.services.turns = TurnRegistry()
    socket, task = await _open(journey)
    await _submit(socket, _turn(turn_id))
    assert model.calls == 1 + MAX_MODEL_DECISIONS_PER_TURN
    await _close(socket, task)


async def test_retransmission_after_the_original_deadline_calls_no_model():
    model = ScriptedModel([final({"action": "answer"})] * 10)
    journey = await build_journey(model=model)
    ledger = journey.services.budgets = InMemoryBudgetLedger()
    turn_id = uuid4()
    await ledger.open(ACCOUNT, turn_id, SESSION, datetime.now(timezone.utc) - timedelta(seconds=25))
    socket, task = await _open(journey)
    responses = await _submit(socket, _turn(turn_id))
    assert model.calls == 0
    assert any(m["type"] == "response.segment" for m in responses)
    await _close(socket, task)


async def test_request_id_reused_for_another_session_is_a_conflict():
    model = ScriptedModel([final({"action": "answer"})] * 10)
    journey = await build_journey(model=model)
    ledger = journey.services.budgets = InMemoryBudgetLedger()
    turn_id = uuid4()
    await ledger.open(ACCOUNT, turn_id, uuid4(), NOW)
    socket, task = await _open(journey)
    responses = await _submit(socket, _turn(turn_id))
    assert responses[-1]["type"] == "error" and responses[-1]["payload"]["code"] == "REQUEST_ID_CONFLICT"
    assert model.calls == 0
    await _close(socket, task)


# ------------------------------------------------------------------ units


def test_observer_sees_every_increment_and_not_empty_grants():
    seen = []
    budget = TurnBudget(observer=lambda b: seen.append((b.model_decisions_used, b.tool_calls_used, b.nested_model_calls)))
    budget.register_model_decision()
    assert budget.reserve_tool_calls(2) == 2
    budget.register_tool_call()
    budget.record_nested_model_call()
    budget.tool_calls_used = budget.max_tool_calls
    assert budget.reserve_tool_calls(1) == 0
    assert seen == [(1, 0, 0), (1, 2, 0), (1, 3, 0), (1, 3, 1)]


def test_usage_round_trip_keeps_the_original_deadline():
    budget = TurnBudget(started_at=NOW, model_decisions_used=2, tool_calls_used=3, nested_model_calls=1)
    resumed = BudgetUsage.of(budget).budget()
    assert resumed.deadline_at == budget.deadline_at
    assert (resumed.model_decisions_used, resumed.tool_calls_used, resumed.nested_model_calls) == (2, 3, 1)


async def test_in_memory_ledger_opens_once_and_only_grows():
    ledger = InMemoryBudgetLedger()
    account, request, session = uuid4(), uuid4(), uuid4()
    first = await ledger.open(account, request, session, NOW)
    await ledger.record(account, request, BudgetUsage(NOW, 3, 4, 1))
    await ledger.record(account, request, BudgetUsage(NOW, 1, 5, 0))
    again = await ledger.open(account, request, session, NOW + timedelta(minutes=1))
    assert first == BudgetUsage(NOW) and again == BudgetUsage(NOW, 3, 5, 1)
    with pytest.raises(IdempotencyConflictError):
        await ledger.open(account, request, uuid4(), NOW)
