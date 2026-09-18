"""ASGI application factory.

Run (after an authorized, locked dependency install):

    uvicorn --factory netra_api.main:create_app

FastAPI is imported only here, so every service and transport module stays
testable without it. Routes:

- GET /health/live — unauthenticated liveness plus registered-capability booleans.
- GET /health/telemetry — tracing counters (created/exported/dropped, flush outcome).
- WS  /v1/ws — authenticated protocol v1 WebSocket (Bearer on the upgrade; M5 implemented).
- POST /v1/sessions, GET /v1/sessions/{id}/sources, POST /v1/sessions/{id}/source —
  M1's reviewed route proposal (see transport/http/sessions.py); pending formal
  M1/M5 sign-off. Every route requires ``Authorization: Bearer``.

Upload and job-status routes are not exposed: the job contract
(shared/contracts/jobs/v1/job.schema.json) is still empty.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any, AsyncIterator, Optional
from uuid import UUID

# Module level on purpose: with postponed annotations FastAPI resolves route
# parameter types from module globals. Imported inside create_app they could
# not be resolved, so ``request: Request`` became a required query parameter
# (every call 422) and the WebSocket parameter failed the same way.
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse

from netra_api.bootstrap import Composition, IntegrationDependencies, build_production
from netra_api.config import Settings
from netra_api.platform.errors import NetraError
from netra_api.transport.http.auth import authenticate
from netra_api.transport.http.health import liveness
from netra_api.transport.http.sessions import create_session, error_response, list_sources, select_source
from netra_api.transport.websocket.endpoint import serve

WEBSOCKET_PATH = "/v1/ws"


def create_app(
    settings: Optional[Settings] = None,
    dependencies: Optional[IntegrationDependencies] = None,
    composition: Optional[Composition] = None,
) -> Any:
    composition = composition or build_production(settings, dependencies)

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        # Bounded final telemetry flush at orderly shutdown; never per turn.
        await asyncio.to_thread(composition.shutdown)

    app = FastAPI(title="Netra API", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @app.get("/health/live")
    async def health_live() -> dict:
        # Liveness never depends on AX, Modal or any provider.
        return liveness(composition.registered)

    @app.get("/health/telemetry")
    async def health_telemetry() -> dict:
        return composition.telemetry_diagnostics()

    def _error(exc: NetraError) -> JSONResponse:
        status, body = error_response(exc)
        return JSONResponse(body, status_code=status)

    async def _principal(request: Request):
        return await authenticate(composition.verifier, request.headers)

    @app.post("/v1/sessions")
    async def post_session(request: Request) -> JSONResponse:
        try:
            principal = await _principal(request)
        except NetraError as exc:
            return _error(exc)
        status, body = await create_session(composition.services, principal)
        return JSONResponse(body, status_code=status)

    @app.get("/v1/sessions/{session_id}/sources")
    async def get_sources(session_id: UUID, request: Request) -> JSONResponse:
        try:
            principal = await _principal(request)
        except NetraError as exc:
            return _error(exc)
        status, body = await list_sources(composition.services, composition.sources, principal, session_id)
        return JSONResponse(body, status_code=status)

    @app.post("/v1/sessions/{session_id}/source")
    async def post_source(session_id: UUID, request: Request) -> JSONResponse:
        try:
            principal = await _principal(request)
            body = await request.json()
        except NetraError as exc:
            return _error(exc)
        except ValueError:
            from netra_api.platform.errors import InvalidRequestError

            return _error(InvalidRequestError("body is not JSON"))
        status, payload = await select_source(composition.services, principal, session_id, body)
        return JSONResponse(payload, status_code=status)

    @app.websocket(WEBSOCKET_PATH)
    async def websocket_endpoint(websocket: WebSocket) -> None:
        await serve(websocket, composition.services, composition.verifier)

    return app
