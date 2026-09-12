"""Tutor agent working state.

Netra has exactly two reasoning agents: Coordinator and Tutor (CLAUDE.md
"Architecture: only two agents"). TutorTurnState is ephemeral, per-handoff
state for the Tutor — distinct from netra_api.session.state.SessionState
(durable, cross-turn) and from
netra_api.coordinator.state.CoordinatorTurnState, which the Tutor must
never receive (CLAUDE.md "Tutor": "must not ... receive the
Coordinator's entire conversation history"; see
netra_api.learning.tutor.policies.assert_not_coordinator_state).
Everything the Tutor knows about the current turn comes from the typed
handoff in netra_api.coordinator.handoff.CoordinatorToTutorHandoff —
this state wraps that handoff plus the Tutor's own bounded execution
budget, and adds no back channel to Coordinator-internal state.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict

from netra_api.coordinator.handoff import CoordinatorToTutorHandoff
from netra_api.coordinator.limits import TurnBudget
from netra_api.platform.auth_context import AuthContext


class TutorTurnState(BaseModel):
    """State scoped to a single Tutor invocation (one Coordinator handoff).

    TurnBudget is reused from netra_api.coordinator.limits rather than
    redefined here: CLAUDE.md "Hard execution limits" applies to every
    agent loop, not only the Coordinator's, and the step-count/deadline/
    cancellation shape a bounded Tutor turn needs is identical.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    handoff: CoordinatorToTutorHandoff
    auth: AuthContext
    budget: TurnBudget
    current_objective: Optional[str] = None
    """The Tutor's own working note on what it is teaching right now;
    never shared back to the Coordinator except through the thin
    TutorToCoordinatorResult (CLAUDE.md: never pass private
    chain-of-thought between agents)."""
