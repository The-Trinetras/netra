"""Tutor agent entry point.

Netra's second and only other reasoning agent (CLAUDE.md "Architecture:
only two agents" — do not add a third). Receives exactly one typed
CoordinatorToTutorHandoff per turn and returns exactly one
TutorToCoordinatorResult; never free-form agent-to-agent chat, never the
Coordinator's full history (CLAUDE.md "Coordinator <-> Tutor
communication must use the versioned typed handoff schema ... Never
implement free-form agent-to-agent chat. Never pass private
chain-of-thought between agents."). Bounded the same way as a
Coordinator turn: a maximum step count, deadline, cancellation path, and
explicit stop condition (CLAUDE.md "Agent loops must always have ...").
"""

from __future__ import annotations

from typing import Protocol

from netra_api.coordinator.handoff import CoordinatorToTutorHandoff, TutorToCoordinatorResult
from netra_api.coordinator.limits import TurnBudget
from netra_api.learning.tutor.state import TutorTurnState
from netra_api.platform.auth_context import AuthContext
from netra_api.platform.errors import TurnBudgetExceededError


class TutorAgent(Protocol):
    """Bounded entry point: one handoff in, one TutorToCoordinatorResult out."""

    async def handle(self, handoff: CoordinatorToTutorHandoff) -> TutorToCoordinatorResult:
        ...


def build_turn_state(
    handoff: CoordinatorToTutorHandoff, auth: AuthContext, budget: TurnBudget
) -> TutorTurnState:
    """Construct the Tutor's own scoped state for one handoff.

    The only place a Tutor turn is seeded from: it takes the handoff
    CLAUDE.md requires and nothing else, so there is no parameter here
    through which Coordinator-internal state could pass (see
    netra_api.learning.tutor.policies.assert_not_coordinator_state).

    budget must be the originating Coordinator turn's budget, so
    delegated work spends the same model-decision, tool-call and deadline
    allowance (CLAUDE.md: "Handoff and delegated work consume the same
    originating budgets and deadline"). Passing a fresh TurnBudget() here
    would hand the Tutor a second full allowance and a restarted clock,
    so the deadline is checked against the handoff rather than trusted.
    """

    if budget.deadline_at > handoff.deadline_at:
        raise TurnBudgetExceededError(
            f"Tutor budget expires at {budget.deadline_at.isoformat()}, "
            f"after the originating turn's deadline of {handoff.deadline_at.isoformat()}; "
            "delegated work must inherit the originating budget, not restart it"
        )

    return TutorTurnState(handoff=handoff, auth=auth, budget=budget)


def run_turn(state: TutorTurnState) -> TutorToCoordinatorResult:
    """Execute a bounded Tutor turn and return its result.

    TODO: dispatch on state.handoff.mode once GroqTutorProvider (see
    netra_api.learning.tutor.providers.groq), quiz generation/validation,
    and evidence resolution are wired to real services. Every step must
    check state.budget.should_stop() first, exactly like
    netra_api.coordinator.router.run_turn.
    """

    raise NotImplementedError("Tutor turn loop is not yet implemented")
