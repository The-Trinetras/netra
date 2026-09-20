"""Persisted turn budget use per request id (decision D-BUDGET, 20 September 2026).

The 4/6/20 budget lives in a TurnBudget in memory. Without this ledger a
retransmission of the same request_id after an API restart would get a fresh
budget. ``open`` records the turn's start once (awaited, before any model
call) and returns what that request_id has already used; TurnBudget's
observer then records every increment. Counters only ever grow, so late or
reordered writes are harmless. PostgreSQL implementation:
session/postgres.py::PostgresBudgetLedger.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from netra_api.coordinator.limits import TurnBudget
from netra_api.platform.errors import IdempotencyConflictError


@dataclass(frozen=True)
class BudgetUsage:
    started_at: datetime
    model_decisions_used: int = 0
    tool_calls_used: int = 0
    nested_model_calls: int = 0

    @classmethod
    def of(cls, budget: TurnBudget) -> "BudgetUsage":
        return cls(budget.started_at, budget.model_decisions_used, budget.tool_calls_used, budget.nested_model_calls)

    def budget(self) -> TurnBudget:
        """A TurnBudget continuing this use: same start, so the same deadline."""

        return TurnBudget(
            started_at=self.started_at,
            model_decisions_used=self.model_decisions_used,
            tool_calls_used=self.tool_calls_used,
            nested_model_calls=self.nested_model_calls,
        )


class BudgetLedger(Protocol):
    async def open(self, account_id: UUID, request_id: UUID, session_id: UUID, now: datetime) -> BudgetUsage:
        """Record the turn's start if new; return the use recorded so far.

        Raises IdempotencyConflictError if the request_id was used for another session.
        """
        ...

    async def record(self, account_id: UUID, request_id: UUID, usage: BudgetUsage) -> None:
        """Raise each stored counter to at least ``usage``'s value."""
        ...


class InMemoryBudgetLedger:
    """For tests and labelled fixture journeys only; forgets everything on restart."""

    def __init__(self) -> None:
        self.rows: dict[tuple[UUID, UUID], tuple[UUID, BudgetUsage]] = {}
        self._lock = asyncio.Lock()

    async def open(self, account_id: UUID, request_id: UUID, session_id: UUID, now: datetime) -> BudgetUsage:
        async with self._lock:
            session, usage = self.rows.setdefault((account_id, request_id), (session_id, BudgetUsage(started_at=now)))
            if session != session_id:
                raise IdempotencyConflictError(str(request_id))
            return usage

    async def record(self, account_id: UUID, request_id: UUID, usage: BudgetUsage) -> None:
        async with self._lock:
            session, stored = self.rows[(account_id, request_id)]
            self.rows[(account_id, request_id)] = (session, BudgetUsage(
                stored.started_at,
                max(stored.model_decisions_used, usage.model_decisions_used),
                max(stored.tool_calls_used, usage.tool_calls_used),
                max(stored.nested_model_calls, usage.nested_model_calls),
            ))
