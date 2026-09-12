"""No model may decide whether an unambiguous command executes."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from netra_api.coordinator.limits import TurnBudget
from netra_api.coordinator.router import RoutingDecision, route_turn
from netra_api.coordinator.state import CoordinatorTurnState
from netra_api.platform.auth_context import AuthContext


class _RecordingRouter:
    """Stands in for the model path and records whether it was consulted."""

    def __init__(self):
        self.route_calls = 0

    def route(self, turn):
        self.route_calls += 1
        return RoutingDecision(kind="answer_directly", direct_answer_text="from the model")


def _turn(utterance, budget=None):
    session_id = uuid4()
    request_id = uuid4()
    return CoordinatorTurnState(
        session_id=session_id,
        request_id=request_id,
        auth=AuthContext(
            account_id=uuid4(),
            session_id=session_id,
            request_id=request_id,
            issued_at=datetime.now(timezone.utc),
        ),
        budget=budget or TurnBudget(),
        original_utterance=utterance,
    )


@pytest.mark.parametrize("utterance", ["stop", "Stop.", "where am I?", "next"])
def test_command_resolves_without_consulting_the_model(utterance):
    router = _RecordingRouter()
    decision = route_turn(_turn(utterance), router)

    assert decision.kind == "deterministic_command"
    assert router.route_calls == 0


def test_command_spends_no_turn_budget():
    """A deterministic command must not consume a model decision."""

    budget = TurnBudget()
    route_turn(_turn("stop", budget=budget), _RecordingRouter())

    assert budget.model_decisions_used == 0
    assert budget.tool_calls_used == 0


def test_non_command_still_reaches_the_model():
    router = _RecordingRouter()
    decision = route_turn(_turn("explain congestion control"), router)

    assert decision.kind == "answer_directly"
    assert router.route_calls == 1


def test_near_miss_reaches_the_model_rather_than_navigating():
    router = _RecordingRouter()
    decision = route_turn(_turn("next question"), router)

    assert decision.kind != "deterministic_command"
    assert router.route_calls == 1


def test_budget_from_deadline_does_not_restart_the_clock():
    """Delegated work inherits the originating deadline (CLAUDE.md:
    "Retries, fallback and delegated work consume the originating turn's
    budget")."""

    now = datetime.now(timezone.utc)
    deadline = now + timedelta(seconds=5)
    budget = TurnBudget.from_deadline(deadline, now=now)

    assert budget.deadline_at == deadline
    assert budget.deadline_seconds == pytest.approx(5.0)
