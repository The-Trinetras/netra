"""Coordinator routing decisions.

Owns study-task routing, source selection, cross-source comparison, and
whether a turn requires Tutor delegation (CLAUDE.md "Coordinator"). This
module keeps the deterministic-first routing entry point; the bounded
model/tool loop lives in netra_api.coordinator.graph.CoordinatorEngine, and
the WebSocket dispatcher applies the same deterministic check before any turn
reaches it.
"""

from __future__ import annotations

from typing import Literal, Optional, Protocol

from pydantic import BaseModel

from netra_api.coordinator.handoff import CoordinatorToTutorHandoff
from netra_api.coordinator.state import CoordinatorTurnState
from netra_api.session.commands import NavigationCommandName, match_deterministic_command

RoutingDecisionKind = Literal[
    "deterministic_command",
    "answer_directly",
    "delegate_to_tutor",
    "request_clarification",
]


class RoutingDecision(BaseModel):
    """Result of the Coordinator deciding how to handle one turn."""

    kind: RoutingDecisionKind
    command: Optional[NavigationCommandName] = None
    """Set only when kind is "deterministic_command"; the Session service
    applies it. No model was consulted to produce it."""
    handoff: Optional[CoordinatorToTutorHandoff] = None
    direct_answer_text: Optional[str] = None


class CoordinatorRouter(Protocol):
    """Bounded entry point for MODEL-based turn routing.

    Only reached for utterances that route_turn could not resolve
    deterministically. Never call this directly from a turn loop; call
    route_turn, which runs the deterministic check first.

    Retained for synchronous callers and tests; the production path is
    netra_api.coordinator.graph.CoordinatorEngine.
    """

    def route(self, turn: CoordinatorTurnState) -> RoutingDecision:
        ...


def route_turn(turn: CoordinatorTurnState, router: CoordinatorRouter) -> RoutingDecision:
    """Route one turn, bypassing model reasoning when the intent is unambiguous.

    CLAUDE.md "Session and execution rules": "Handle unambiguous commands
    as application logic, bypassing LLM reasoning." coordinator.md:
    "Route unambiguous control commands before model reasoning."

    This is the only supported entry point for routing a turn, and the
    ordering is the point of it: the deterministic check runs first and
    returns before any model decision is dispatched or any budget is
    spent, so no model ever decides whether "stop" should stop.

    The original utterance is passed through untouched; matching uses a
    folded comparison key and never rewrites what the student said
    (netra_api.session.commands.normalize_utterance).
    """

    command = match_deterministic_command(turn.original_utterance)
    if command is not None:
        return RoutingDecision(kind="deterministic_command", command=command)

    return router.route(turn)
