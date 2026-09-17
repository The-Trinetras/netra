"""Netra FastAPI application entrypoint."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from sqlalchemy import text

from netra_api.bootstrap import ApplicationResources, build_application_resources
from netra_api.config import Settings

ResourceFactory = Callable[[Settings], ApplicationResources]


def create_app(
    settings: Settings | None = None,
    *,
    resource_factory: ResourceFactory = build_application_resources,
) -> FastAPI:
    """Construct the API without making network calls or mutating schema."""

    resources = resource_factory(settings or Settings())

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        application.state.resources = resources
        try:
            yield
        finally:
            await resources.close()

    application = FastAPI(title="Netra API", version="0.1.0", lifespan=lifespan)

    @application.get("/health/live", tags=["health"])
    async def liveness() -> dict[str, str]:
        return {"status": "live", "service": "netra-api"}

    @application.get("/health/ready", tags=["health"])
    async def readiness(request: Request) -> dict[str, Any]:
        runtime: ApplicationResources = request.app.state.resources
        try:
            async with runtime.engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        except Exception:
            # Do not disclose connection details or provider exceptions.
            raise HTTPException(
                status_code=503,
                detail={"status": "unavailable", "dependency": "postgresql"},
            ) from None
        return {"status": "ready", "dependencies": {"postgresql": "ready"}}

    return application


app = create_app()

__all__ = ["app", "create_app"]
