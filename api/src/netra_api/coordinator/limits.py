"""Hard execution limits for the Coordinator agent.

These are application-enforced limits, not prompt suggestions (see
CLAUDE.md "Hard execution limits"). Every Coordinator loop must consult
a TurnBudget before each model decision or tool call, and must stop when
it is exhausted, expired, or cancelled.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from netra_api.platform.errors import TurnBudgetExceededError

MAX_MODEL_DECISIONS_PER_TURN = 4
MAX_TOOL_CALLS_PER_TURN = 6
ANSWER_DEADLINE_SECONDS = 20.0


@dataclass
class TurnBudget:
    """Tracks one turn's remaining decisions, tool calls, and deadline.

    A mutable runtime tracker (not a wire model), so it is a plain
    dataclass rather than a Pydantic model.
    """

    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    max_model_decisions: int = MAX_MODEL_DECISIONS_PER_TURN
    max_tool_calls: int = MAX_TOOL_CALLS_PER_TURN
    deadline_seconds: float = ANSWER_DEADLINE_SECONDS
    model_decisions_used: int = 0
    tool_calls_used: int = 0
    cancelled: bool = False

    @classmethod
    def from_deadline(cls, deadline_at: datetime, now: Optional[datetime] = None) -> "TurnBudget":
        """Build a budget that expires at an absolute deadline.

        Needed because a delegated turn is handed a deadline, not a
        duration: CoordinatorToTutorHandoff carries deadline_at, and
        CLAUDE.md requires that "Retries, fallback and delegated work
        consume the originating turn's budget". Constructing a plain
        TurnBudget() on the Tutor side would silently restart the 20
        seconds, which is exactly the reset this forbids.

        Prefer sharing the originating TurnBudget instance outright; use
        this only where the originating instance cannot be passed, such
        as across a process boundary.
        """

        started_at = now or datetime.now(timezone.utc)
        return cls(
            started_at=started_at,
            deadline_seconds=(deadline_at - started_at).total_seconds(),
        )

    @property
    def deadline_at(self) -> datetime:
        return self.started_at + timedelta(seconds=self.deadline_seconds)

    def cancel(self) -> None:
        """Explicit cancellation path, e.g. on response.cancel or a superseding turn."""

        self.cancelled = True

    def is_expired(self, now: Optional[datetime] = None) -> bool:
        return (now or datetime.now(timezone.utc)) >= self.deadline_at

    def should_stop(self, now: Optional[datetime] = None) -> bool:
        """Explicit stop condition a Coordinator loop must check before every step."""

        return (
            self.cancelled
            or self.is_expired(now)
            or self.model_decisions_used >= self.max_model_decisions
            or self.tool_calls_used >= self.max_tool_calls
        )

    def register_model_decision(self) -> None:
        if self.cancelled or self.is_expired():
            raise TurnBudgetExceededError("turn already cancelled or expired")
        if self.model_decisions_used >= self.max_model_decisions:
            raise TurnBudgetExceededError(
                f"max model decisions per turn ({self.max_model_decisions}) exceeded"
            )
        self.model_decisions_used += 1

    def register_tool_call(self) -> None:
        if self.cancelled or self.is_expired():
            raise TurnBudgetExceededError("turn already cancelled or expired")
        if self.tool_calls_used >= self.max_tool_calls:
            raise TurnBudgetExceededError(
                f"max tool calls per turn ({self.max_tool_calls}) exceeded"
            )
        self.tool_calls_used += 1
