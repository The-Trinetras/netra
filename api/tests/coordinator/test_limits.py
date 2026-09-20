from datetime import datetime, timedelta, timezone

import pytest

from netra_api.coordinator.limits import (
    ANSWER_DEADLINE_SECONDS,
    MAX_MODEL_DECISIONS_PER_TURN,
    MAX_TOOL_CALLS_PER_TURN,
    TurnBudget,
)
from netra_api.platform.errors import TurnBudgetExceededError


def test_default_limits_match_claude_md():
    """D-BUDGET-2 (20 September 2026): the AgentSpec's 8/12/45 was adopted.

    CLAUDE.md and these constants must say the same thing; this test is what
    fails if one moves without the other.
    """

    assert MAX_MODEL_DECISIONS_PER_TURN == 8
    assert MAX_TOOL_CALLS_PER_TURN == 12
    assert ANSWER_DEADLINE_SECONDS == 45.0


def test_register_model_decision_stops_at_max():
    budget = TurnBudget()
    for _ in range(MAX_MODEL_DECISIONS_PER_TURN):
        budget.register_model_decision()
    with pytest.raises(TurnBudgetExceededError):
        budget.register_model_decision()


def test_register_tool_call_stops_at_max():
    budget = TurnBudget()
    for _ in range(MAX_TOOL_CALLS_PER_TURN):
        budget.register_tool_call()
    with pytest.raises(TurnBudgetExceededError):
        budget.register_tool_call()


def test_should_stop_when_cancelled():
    budget = TurnBudget()
    assert budget.should_stop() is False
    budget.cancel()
    assert budget.should_stop() is True


def test_should_stop_when_expired():
    budget = TurnBudget(started_at=datetime.now(timezone.utc) - timedelta(seconds=100))
    assert budget.is_expired() is True
    assert budget.should_stop() is True


def test_register_decision_after_expiry_raises():
    budget = TurnBudget(started_at=datetime.now(timezone.utc) - timedelta(seconds=100))
    with pytest.raises(TurnBudgetExceededError):
        budget.register_model_decision()
