"""Async PostgreSQL access shared by M1-owned repositories.

Runtime baseline: application access uses SQLAlchemy with postgresql+asyncpg;
LangGraph checkpointing uses Psycopg on separate connections and is not
configured here. M2 owns shared database plumbing and Alembic migrations, so
this module deliberately does not create tables: ``M1_METADATA`` describes the
PROPOSED shape of M1-owned tables for M2's reviewed migration, and each
repository fails at query time if the reviewed migration has not been applied.

Transactions are short and never span external calls (CLAUDE.md): a repository
method opens ``engine.begin()``, performs its statements and commits.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import MetaData

from netra_api.platform.errors import ResourceUnavailableError

M1_METADATA = MetaData()
"""Proposed M1-owned table definitions (identity, session, idempotency,
result sets, dialogue). Not a migration and not applied automatically."""

ASYNC_DRIVER_PREFIX = "postgresql+asyncpg://"


def create_engine(database_url: str, **engine_options: Any):
    """Create the async engine for application access.

    Rejects any URL that is not postgresql+asyncpg so a misconfiguration
    cannot silently select a different driver or database kind.
    """

    if not database_url.startswith(ASYNC_DRIVER_PREFIX):
        raise ResourceUnavailableError("database URL must use the postgresql+asyncpg driver")

    from sqlalchemy.ext.asyncio import create_async_engine

    return create_async_engine(database_url, pool_pre_ping=True, **engine_options)


def create_session_factory(engine: Any):
    """Session factory for M2 repositories (sessions do not expire on commit)."""

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def dispose_engine(engine: Any) -> None:
    await engine.dispose()
