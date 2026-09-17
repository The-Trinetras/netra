"""Explicit construction and lifecycle for API-owned runtime resources."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from netra_api.config import Settings
from netra_api.platform.database import (
    create_engine,
    create_session_factory,
    dispose_engine,
)


@dataclass(frozen=True)
class ApplicationResources:
    """Infrastructure owned by one FastAPI application instance."""

    settings: Settings
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]

    async def close(self) -> None:
        await dispose_engine(self.engine)


def build_application_resources(settings: Settings) -> ApplicationResources:
    """Validate configuration and construct lazy database resources.

    SQLAlchemy does not connect here. Database reachability is exposed by the
    readiness endpoint, and Alembic remains the only schema migration owner.
    """

    engine = create_engine(settings)
    return ApplicationResources(
        settings=settings,
        engine=engine,
        sessions=create_session_factory(engine),
    )


__all__ = ["ApplicationResources", "build_application_resources"]
