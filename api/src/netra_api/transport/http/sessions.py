"""Session creation, source listing and explicit source selection over HTTP.

Implements M1's reviewed route proposal (docs/team/handoffs/M1.md "Route
proposals for M5 review"); M5 implemented the Bearer presentation on its side.
The HTTP shapes below are integration-branch proposals pending formal M1/M5
sign-off; no WebSocket protocol field changes.

- ``POST /v1/sessions`` — server-mints a session id, Identity binds it to the
  verified principal, Session service initialises canonical state. Returns the
  typed ``session.snapshot`` payload and the new ``session_id``.
- ``GET /v1/sessions/{session_id}/sources`` — the account's sources with their
  active version (read only; the session binding scopes the caller).
- ``POST /v1/sessions/{session_id}/source`` — explicit source pin with
  ``request_id`` replay and ``expected_session_version`` semantics, exactly
  like a navigation command. A newly activated version never moves a session;
  only this call changes the pin.

Framework-neutral: each handler returns ``(status, body)``. Failures use the
typed ``error`` payload (code/message/retryable), never exception text.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from netra_api.platform.auth_context import AuthenticatedPrincipal
from netra_api.platform.awaitables import maybe_await
from netra_api.platform.errors import (
    ErrorCode,
    InvalidRequestError,
    NetraError,
    ResourceUnavailableError,
    SessionVersionConflictError,
    error_code_for,
)
from netra_api.transport.websocket.dispatcher import TransportServices
from netra_api.transport.websocket.serializer import error_payload_for
from netra_api.transport.websocket.snapshots import build_session_snapshot

_STATUS_BY_CODE = {
    ErrorCode.AUTH_REQUIRED: 401,
    ErrorCode.AUTHORIZATION_DENIED: 403,
    ErrorCode.SESSION_VERSION_CONFLICT: 409,
    ErrorCode.REQUEST_ID_CONFLICT: 409,
    ErrorCode.STALE_REQUEST: 409,
    ErrorCode.INVALID_REQUEST: 422,
    ErrorCode.RESOURCE_UNAVAILABLE: 503,
    ErrorCode.PROVIDER_UNAVAILABLE: 503,
}


def error_response(exc: NetraError) -> tuple[int, dict[str, Any]]:
    current = exc.actual_version if isinstance(exc, SessionVersionConflictError) else None
    payload = error_payload_for(exc, current_session_version=current).model_dump(mode="json", exclude_none=True)
    return _STATUS_BY_CODE.get(error_code_for(exc), 500), {"error": payload}


class SourceSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    """Stable per logical selection; a retransmission replays its recorded result."""
    source_version_id: UUID
    expected_session_version: int = Field(ge=0)


async def create_session(services: TransportServices, principal: AuthenticatedPrincipal) -> tuple[int, dict[str, Any]]:
    try:
        session_id = uuid4()
        await services.identity.bind_new_session(principal, session_id)
        auth = await services.identity.resolve_auth_context(principal, session_id, uuid4())
        state = await services.sessions.create_session(auth)
    except NetraError as exc:
        return error_response(exc)
    return 201, {"session_id": str(session_id), "snapshot": build_session_snapshot(state).model_dump(mode="json")}


async def list_sources(services: TransportServices, sources: Any, principal: AuthenticatedPrincipal,
                       session_id: UUID) -> tuple[int, dict[str, Any]]:
    try:
        if sources is None:
            raise ResourceUnavailableError("source storage is not registered")
        auth = await services.identity.resolve_auth_context(principal, session_id, uuid4())
        items = []
        for source in await maybe_await(sources.list_sources(auth)):
            active = await maybe_await(sources.get_active_version(auth, source.source_id))
            items.append({
                "source_id": str(source.source_id),
                "title": source.title,
                "active_source_version_id": str(active.source_version_id) if active else None,
                "active_version_number": active.version_number if active else None,
            })
    except NetraError as exc:
        return error_response(exc)
    return 200, {"sources": items}


def _selection_fingerprint(selection: SourceSelection) -> str:
    canonical = {"type": "source.select", "source_version_id": str(selection.source_version_id),
                 "expected_session_version": selection.expected_session_version}
    return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode("utf-8")).hexdigest()


async def select_source(services: TransportServices, principal: AuthenticatedPrincipal, session_id: UUID,
                        body: Any) -> tuple[int, dict[str, Any]]:
    try:
        try:
            selection = SourceSelection.model_validate(body)
        except ValidationError as exc:
            field = ".".join(str(p) for p in exc.errors()[0].get("loc", ())) if exc.errors() else None
            raise InvalidRequestError("invalid source selection", field=field) from None
        auth = await services.identity.resolve_auth_context(principal, session_id, selection.request_id)
        # Canonical UUID string: a differently spelled identical UUID is the same pin.
        version = str(selection.source_version_id)

        async def decide(state):
            proposed = await services.sessions.pin_source(state, auth, version)
            return proposed, lambda final: {"snapshot": build_session_snapshot(final).model_dump(mode="json")}

        outcome = await services.sessions.handle_request(
            auth, request_id=selection.request_id, payload_fingerprint=_selection_fingerprint(selection),
            expected_version=selection.expected_session_version, decide=decide)
        if not outcome.replayed:
            # A successful explicit open supersedes answers/audio from the
            # previous reading context. Replaying the pin must not cancel a
            # later turn or generation.
            services.turns.cancel_session(auth.account_id, auth.session_id, "navigation")
            services.generations.cancel_speaking(auth.session_id, "navigation")
    except NetraError as exc:
        return error_response(exc)
    return 200, {**outcome.result, "replayed": outcome.replayed}


__all__ = ["create_session", "error_response", "list_sources", "select_source"]
