from datetime import datetime, timezone
from uuid import uuid4

import pytest

from netra_api.coordinator.limits import TurnBudget
from netra_api.coordinator.state import CoordinatorTurnState
from netra_api.learning.tutor.policies import CoordinatorStateLeakError, assert_not_coordinator_state
from netra_api.platform.auth_context import AuthContext


def test_assert_not_coordinator_state_raises_for_coordinator_state():
    auth = AuthContext(account_id=uuid4(), session_id=uuid4(), request_id=uuid4(), issued_at=datetime.now(timezone.utc))
    coordinator_state = CoordinatorTurnState(
        session_id=uuid4(),
        request_id=uuid4(),
        auth=auth,
        budget=TurnBudget(),
        original_utterance="Can you explain congestion control?",
    )
    with pytest.raises(CoordinatorStateLeakError):
        assert_not_coordinator_state(coordinator_state)


def test_assert_not_coordinator_state_allows_other_objects():
    assert_not_coordinator_state({"handoff": "some scoped payload"})
    assert_not_coordinator_state(None)
