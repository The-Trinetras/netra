"""POST /v1/device-credentials: exchange a one-time access code (D-CRED).

Contract: shared/contracts/http/v1/device_credential.schema.json. The route
takes no credential. Framework-neutral like sessions.py: returns
``(status, body)``; failures use the typed error payload, never exception text.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from netra_api.identity.access_codes import ExchangeRequest
from netra_api.platform.errors import InvalidRequestError, NetraError
from netra_api.transport.http.sessions import error_response


async def exchange_device_credential(identity: Any, body: Any) -> tuple[int, dict[str, Any]]:
    try:
        request = ExchangeRequest.model_validate(body)
    except ValidationError:
        return error_response(InvalidRequestError("invalid access-code exchange"))
    try:
        issued = await identity.exchange_access_code(request)
    except NetraError as exc:
        return error_response(exc)
    return 201, issued.model_dump(mode="json")
