"""ASGI application factory.

Run (after an authorized, locked dependency install):

    uvicorn --factory netra_api.main:create_app

FastAPI is imported only here, so every service and transport module stays
testable without it. Routes:

- GET /health/live — unauthenticated liveness plus registered-capability booleans.
- WS  /v1/ws — authenticated protocol v1 WebSocket (PROPOSED path; M5 review).

Session creation, source selection/upload and job-status HTTP routes are not
exposed: no committed contract defines them yet (see docs/team/handoffs/M1.md).
"""

from __future__ import annotations

from typing import Any, Optional

from netra_api.bootstrap import Composition, IntegrationDependencies, build_production
from netra_api.config import Settings
from netra_api.transport.http.health import liveness
from netra_api.transport.websocket.endpoint import serve

WEBSOCKET_PATH = "/v1/ws"


def create_app(
    settings: Optional[Settings] = None,
    dependencies: Optional[IntegrationDependencies] = None,
    composition: Optional[Composition] = None,
) -> Any:
    from fastapi import FastAPI, WebSocket

    composition = composition or build_production(settings, dependencies)
    app = FastAPI(title="Netra API", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health/live")
    async def health_live() -> dict:
        return liveness(composition.registered)

    @app.websocket(WEBSOCKET_PATH)
    async def websocket_endpoint(websocket: WebSocket) -> None:
        await serve(websocket, composition.services, composition.verifier)

    return app
