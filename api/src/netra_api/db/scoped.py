"""Long-lived facades over repositories that need one AsyncSession per operation.

M2's repositories and retrieval graph are constructed around a single
``AsyncSession``. The application composition root lives for the whole
process and serves concurrent requests, so it must never hold one shared
session: two requests would interleave statements in one transaction (see
netra_api.db.transactions). ``SessionScoped`` gives every awaited method call
its own session, builds the target around it, runs the call and closes the
session, so each operation owns exactly its own short transactions.

Results must be plain domain values (M2 repositories return Pydantic models),
never ORM instances that outlive their session.
"""

from __future__ import annotations

import inspect
from typing import Any, Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class SessionScoped:
    """Proxy whose every method call runs on a fresh session."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], build: Callable[[AsyncSession], Any]) -> None:
        self._sessions = sessions
        self._build = build

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)

        async def call(*args: Any, **kwargs: Any) -> Any:
            async with self._sessions() as session:
                result = getattr(self._build(session), name)(*args, **kwargs)
                if inspect.isawaitable(result):
                    result = await result
                return result

        call.__name__ = name
        return call


__all__ = ["SessionScoped"]
