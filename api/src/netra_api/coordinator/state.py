"""Coordinator agent working state.

Netra has exactly two reasoning agents: Coordinator and Tutor (CLAUDE.md
"Architecture: only two agents"). Do not add additional agent state
modules for other subsystems merely because they use AI.

CoordinatorTurnState is ephemeral, per-turn state for the Coordinator
agent. This is distinct from netra_api.session.state.SessionState, which
is the durable, cross-turn session record. The Coordinator reads/writes
session state only through SessionService, never directly.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from netra_api.coordinator.limits import TurnBudget
from netra_api.platform.auth_context import AuthContext


class CoordinatorTurnState(BaseModel):
    """State scoped to a single Coordinator turn (one student utterance)."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    session_id: UUID
    request_id: UUID
    auth: AuthContext
    budget: TurnBudget
    original_utterance: str
    pending_handoff_id: Optional[UUID] = None
    """Set once the Coordinator has delegated to the Tutor for this turn."""
