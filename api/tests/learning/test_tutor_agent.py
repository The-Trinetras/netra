import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from netra_api.coordinator.handoff import CoordinatorToTutorHandoff
from netra_api.coordinator.limits import TurnBudget
from netra_api.learning.tutor.agent import build_turn_state, run_turn
from netra_api.platform.auth_context import AuthContext
from netra_api.platform.errors import TurnBudgetExceededError

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_HANDOFF = REPO_ROOT / "shared" / "contracts" / "examples" / "handoffs" / "coordinator_to_tutor.json"


def _load_handoff(deadline_at: datetime | None = None) -> CoordinatorToTutorHandoff:
    """Parse the committed example, optionally with a live deadline.

    The example pins deadline_at to a fixed past timestamp, which is right
    for a contract fixture but useless for budget behaviour: any budget
    faithful to it has already expired. Tests that exercise the budget
    move the deadline forward and change nothing else.
    """

    data = json.loads(EXAMPLE_HANDOFF.read_text())
    if deadline_at is not None:
        data["deadline_at"] = deadline_at.isoformat()
    return CoordinatorToTutorHandoff.model_validate(data)


def _live_handoff() -> CoordinatorToTutorHandoff:
    return _load_handoff(deadline_at=datetime.now(timezone.utc) + timedelta(seconds=20))


def _auth(handoff: CoordinatorToTutorHandoff) -> AuthContext:
    return AuthContext(
        account_id=uuid4(),
        session_id=handoff.session_id,
        request_id=handoff.request_id,
        issued_at=datetime.now(timezone.utc),
    )


def _inherited_budget(handoff: CoordinatorToTutorHandoff) -> TurnBudget:
    """The budget a Coordinator would hand down: it expires when the
    originating turn does, not 20 seconds from whenever the Tutor starts."""

    return TurnBudget.from_deadline(handoff.deadline_at)


def test_build_turn_state_wraps_handoff_and_budget():
    handoff = _live_handoff()
    budget = _inherited_budget(handoff)
    state = build_turn_state(handoff, _auth(handoff), budget)
    assert state.handoff is handoff
    assert state.budget is budget


def test_build_turn_state_rejects_a_restarted_budget():
    """CLAUDE.md: "Retries, fallback and delegated work consume the
    originating turn's budget." A fresh TurnBudget() starts its own 20
    seconds, which runs past the originating turn's deadline."""

    handoff = _load_handoff(deadline_at=datetime.now(timezone.utc) + timedelta(seconds=5))
    with pytest.raises(TurnBudgetExceededError):
        build_turn_state(handoff, _auth(handoff), TurnBudget())


def test_delegated_budget_shares_the_coordinator_counters():
    """Sharing the instance is what makes tool calls spent by the Tutor
    count against the originating turn."""

    handoff = _live_handoff()
    budget = _inherited_budget(handoff)
    budget.register_tool_call()

    state = build_turn_state(handoff, _auth(handoff), budget)
    state.budget.register_tool_call()

    assert budget.tool_calls_used == 2


def test_run_turn_is_not_yet_implemented():
    handoff = _live_handoff()
    state = build_turn_state(handoff, _auth(handoff), _inherited_budget(handoff))
    with pytest.raises(NotImplementedError):
        run_turn(state)
