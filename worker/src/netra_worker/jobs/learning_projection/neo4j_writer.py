"""Neo4j adapter for the factual learning projection (neo4j==6.3.0 async driver).

Implements netra_worker.jobs.learning_projection.neo4j.FactualLearningGraphWriter.
Runs the reviewed ATTEMPTED_EDGE_CYPHER with bound parameters through
``AsyncDriver.execute_query`` (write routing, explicit database, a server-side
transaction timeout and a client-side bound). Driver objects never leave this
module; callers see only ProjectionWrite values or Netra failure classes.

Failure classes (backend-data.md "Do not retry denied access, invalid input
... indiscriminately"): authentication, configuration and Cypher errors are
PermanentJobError (dead-lettered: retrying cannot fix them); service
unavailability, transient database errors and timeouts propagate as
retryable. Neo4j is a rebuildable projection, so none of these ever touch the
committed PostgreSQL attempt.

Configuration comes only from NETRA_NEO4J_* environment variables; the
password is a secret and is never logged, traced or included in errors.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from netra_worker.jobs.learning_projection.neo4j import (
    ATTEMPTED_EDGE_CYPHER,
    AttemptedEdge,
    ProjectionWrite,
)
from netra_worker.runtime.errors import PermanentJobError


class LearningProjectionSettings(BaseSettings):
    """Neo4j projection configuration. Unset URI means the projection is disabled."""

    model_config = SettingsConfigDict(env_prefix="NETRA_NEO4J_", env_file=None, extra="ignore")

    uri: Optional[str] = None
    user: Optional[str] = None
    password: Optional[SecretStr] = None
    database: str = "neo4j"
    query_timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    workers: int = Field(default=1, ge=0, le=8)

    @property
    def configured(self) -> bool:
        return bool(self.uri and self.user and self.password)


def _permanent_errors() -> tuple[type[BaseException], ...]:
    from neo4j.exceptions import AuthError, ConfigurationError, ConstraintError, CypherSyntaxError, CypherTypeError

    return (AuthError, ConfigurationError, ConstraintError, CypherSyntaxError, CypherTypeError)


class Neo4jFactualGraphWriter:
    """FactualLearningGraphWriter over an AsyncDriver-compatible object."""

    def __init__(self, driver: Any, *, database: str, query_timeout_seconds: float) -> None:
        if query_timeout_seconds <= 0:
            raise ValueError("query_timeout_seconds must be positive")
        self._driver = driver
        self._database = database
        self._timeout = query_timeout_seconds

    @classmethod
    def from_settings(cls, settings: LearningProjectionSettings) -> "Neo4jFactualGraphWriter":
        if not settings.configured:
            raise ValueError("the Neo4j learning projection is not configured")
        from neo4j import AsyncGraphDatabase  # imported only when actually configured

        driver = AsyncGraphDatabase.driver(
            settings.uri, auth=(settings.user, settings.password.get_secret_value()))
        return cls(driver, database=settings.database, query_timeout_seconds=settings.query_timeout_seconds)

    async def upsert_attempted(self, edge: AttemptedEdge) -> ProjectionWrite:
        from neo4j import Query, RoutingControl

        parameters = {
            "account_id": str(edge.account_id),
            "concept_id": edge.concept_id,
            "attempt_id": str(edge.attempt_id),
            "outcome": edge.outcome,
            "hints_used": edge.hints_used,
            "evaluated_by": edge.evaluated_by,
            "occurred_at": edge.occurred_at.isoformat(),
        }
        try:
            result = await asyncio.wait_for(
                self._driver.execute_query(
                    Query(ATTEMPTED_EDGE_CYPHER, timeout=self._timeout),
                    parameters_=parameters,
                    routing_=RoutingControl.WRITE,
                    database_=self._database,
                ),
                # Client bound slightly above the server-side transaction timeout.
                timeout=self._timeout + 1.0,
            )
        except Exception as exc:
            if isinstance(exc, _permanent_errors()):
                # Type name only: driver messages may echo configuration.
                raise PermanentJobError(f"neo4j projection refused: {type(exc).__name__}") from None
            raise
        return ProjectionWrite.PROJECTED if result.records else ProjectionWrite.CONCEPT_NOT_PROJECTED

    async def close(self) -> None:
        await self._driver.close()


__all__ = ["LearningProjectionSettings", "Neo4jFactualGraphWriter"]
