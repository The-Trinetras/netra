"""Coordinator agent working state and turn outcome.

Netra has exactly two reasoning agents: Coordinator and Tutor. Do not add
agent state modules for other subsystems merely because they use AI.

CoordinatorTurnState is ephemeral, per-turn state for the Coordinator. It is
distinct from netra_api.session.state.SessionState, the durable cross-turn
record, which the Coordinator reads through SessionService and never writes
directly: a TurnOutcome describes the session delta, and the transport commits
it through SessionService.commit_turn.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from netra_api.coordinator.limits import TurnBudget
from netra_api.platform.auth_context import AuthContext
from netra_api.session.outputs import PlannedQuestion, PlannedSegment
from netra_api.session.state import PendingQuestionRef, SessionState


class CoordinatorTurnState(BaseModel):
    """State scoped to a single Coordinator turn (one accepted student utterance)."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    session_id: UUID
    request_id: UUID
    auth: AuthContext
    budget: TurnBudget
    original_utterance: str
    pending_handoff_id: Optional[UUID] = None
    """Set once the Coordinator has delegated to the Tutor for this turn."""
    session: Optional[SessionState] = None
    """Canonical session state read when the turn was accepted."""
    companion_source_version_ids: frozenset[str] = frozenset()


OutcomeKind = Literal["answer", "tutor", "clarification", "gap", "limited", "failed", "cancelled"]


@dataclass(frozen=True)
class TurnOutcome:
    kind: OutcomeKind
    segments: tuple[PlannedSegment, ...] = ()
    question: Optional[PlannedQuestion] = None
    lesson_id: Optional[UUID] = None
    """Set when a Tutor lesson is active after this turn."""
    pending_question: Optional[PendingQuestionRef] = None
    """The pending question after this turn, when the Tutor left one waiting."""
    clear_pending_question: bool = False
    reply_role: Literal["coordinator", "tutor"] = "coordinator"
