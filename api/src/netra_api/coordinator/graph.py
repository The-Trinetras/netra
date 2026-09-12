"""Coordinator turn execution loop shape.

Not wired to LangGraph or any persistence yet (out of scope for this
boilerplate task). This module only fixes the shape of one bounded
Coordinator turn: a sequence of steps bounded by TurnBudget, with an
explicit stop condition and cancellation path (CLAUDE.md "Agent loops
must always have... a maximum step count, deadline, cancellation path,
quota/budget check, explicit stop condition").
"""

from __future__ import annotations

from typing import Protocol

from netra_api.coordinator.router import RoutingDecision
from netra_api.coordinator.state import CoordinatorTurnState


class CoordinatorTurnStep(Protocol):
    """One bounded step of a Coordinator turn (one model decision or one tool call)."""

    def run(self, turn: CoordinatorTurnState) -> None:
        ...


def run_turn(turn: CoordinatorTurnState) -> RoutingDecision:
    """Execute a bounded Coordinator turn and return its routing decision.

    TODO: implement the step loop once CoordinatorRouter
    (coordinator/router.py) has a concrete implementation. Every step
    must check turn.budget.should_stop() first and stop immediately when
    it returns True.
    """

    raise NotImplementedError("Coordinator turn loop is not yet implemented")
