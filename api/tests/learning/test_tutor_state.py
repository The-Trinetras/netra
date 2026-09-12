import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from netra_api.coordinator.handoff import CoordinatorToTutorHandoff
from netra_api.coordinator.limits import TurnBudget
from netra_api.learning.tutor.state import TutorTurnState
from netra_api.platform.auth_context import AuthContext

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_HANDOFF = REPO_ROOT / "shared" / "contracts" / "examples" / "handoffs" / "coordinator_to_tutor.json"


def _load_handoff() -> CoordinatorToTutorHandoff:
    data = json.loads(EXAMPLE_HANDOFF.read_text())
    return CoordinatorToTutorHandoff.model_validate(data)


def test_tutor_turn_state_wraps_the_handoff_verbatim():
    handoff = _load_handoff()
    auth = AuthContext(account_id=uuid4(), session_id=handoff.session_id, request_id=handoff.request_id, issued_at=datetime.now(timezone.utc))
    state = TutorTurnState(handoff=handoff, auth=auth, budget=TurnBudget())
    assert state.handoff.mode == "explain"
    assert state.current_objective is None


def test_tutor_turn_state_has_no_field_named_after_coordinator_internals():
    field_names = set(TutorTurnState.model_fields)
    assert "coordinator_turn_state" not in field_names
    assert "chain_of_thought" not in field_names
