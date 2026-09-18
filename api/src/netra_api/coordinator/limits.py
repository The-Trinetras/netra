"""Hard execution limits for the Coordinator agent.

These are application-enforced limits, not prompt suggestions (see
CLAUDE.md "Hard execution limits"). Every Coordinator loop must consult
a TurnBudget before each model decision or tool call, and must stop when
it is exhausted, expired, or cancelled.
"""

from __future__ import annotations

import asyncio
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
    nested_model_calls: int = 0
    """Model calls made INSIDE tools (e.g. a video-description provider).
    Recorded for inspection but NOT counted against max_model_decisions:
    whether they should count is an open M1/M3/M4 decision (the AgentSpec
    proposes counting them; the approved 4/6/20 baseline does not say).
    Recording them keeps that decision evaluable without silently making it."""
    _cancelled_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False, compare=False)

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
        self._cancelled_event.set()

    async def wait_cancelled(self) -> None:
        """Resolve when cancel() is called; used to abandon in-flight dispatch."""

        await self._cancelled_event.wait()

    def remaining_seconds(self, now: Optional[datetime] = None) -> float:
        return max(0.0, (self.deadline_at - (now or datetime.now(timezone.utc))).total_seconds())

    @property
    def remaining_model_decisions(self) -> int:
        return max(0, self.max_model_decisions - self.model_decisions_used)

    @property
    def remaining_tool_calls(self) -> int:
        return max(0, self.max_tool_calls - self.tool_calls_used)

    def reserve_tool_calls(self, count: int) -> int:
        """Atomically reserve up to ``count`` tool invocations; return how many were granted.

        Concurrent dispatch counts every call before any starts, so a
        parallel batch can never overshoot the shared limit. Calls beyond
        the grant are not dispatched at all.
        """

        if self.cancelled or self.is_expired():
            return 0
        granted = min(count, self.remaining_tool_calls)
        self.tool_calls_used += granted
        return granted

    def for_retransmission(self) -> "TurnBudget":
        """Budget for retransmitting the SAME logical action after a disconnect.

        Spent model decisions, tool calls, nested calls and the original
        deadline carry over unchanged; only the disconnect's cancellation is
        cleared so the action can continue. Never used for a new request_id.
        """

        return TurnBudget(
            started_at=self.started_at,
            max_model_decisions=self.max_model_decisions,
            max_tool_calls=self.max_tool_calls,
            deadline_seconds=self.deadline_seconds,
            model_decisions_used=self.model_decisions_used,
            tool_calls_used=self.tool_calls_used,
            nested_model_calls=self.nested_model_calls,
        )

    def record_nested_model_call(self) -> None:
        self.nested_model_calls += 1

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
