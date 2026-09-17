"""Async SQLAlchemy application database lifecycle.

The checkpointing connection used by LangGraph is intentionally separate.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from netra_api.config import Settings


def create_engine(settings: Settings | None = None) -> AsyncEngine:
    settings = settings or Settings()
    if not settings.database_url.startswith("postgresql+asyncpg://"):
        raise ValueError("database_url must use the postgresql+asyncpg scheme")
    return create_async_engine(settings.database_url, pool_pre_ping=True, pool_size=settings.database_pool_size)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def session_scope(factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with factory() as session:
        yield session


async def dispose_engine(engine: AsyncEngine) -> None:
    await engine.dispose()
