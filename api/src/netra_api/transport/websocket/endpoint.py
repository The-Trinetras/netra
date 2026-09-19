"""WebSocket endpoint: verify before accept, then dispatch in order.

Framework-neutral: ``serve`` works with any socket object exposing the
Starlette/FastAPI WebSocket methods used below (headers, accept, receive,
send_text, send_bytes, close), so the full path is testable without FastAPI.

Close codes:
- 1008 (policy violation) before accept when the credential fails verification
  (Starlette turns a pre-accept close into an HTTP 403 upgrade rejection);
- 1007 (invalid payload) for frames that cannot even carry correlation ids,
  since no typed error envelope can be addressed without them, and for a
  binary frame whose microphone header cannot be read (D-MIC). Readable
  microphone frames go to Connection.handle_bytes; server audio framing is
  never accepted from the client.
"""

from __future__ import annotations

import logging
from typing import Any

from netra_api.identity.service import CredentialVerifier
from netra_api.platform.errors import InvalidRequestError, NetraError
from netra_api.transport.audio.microphone import UnreadableMicrophoneFrame
from netra_api.transport.http.auth import authenticate
from netra_api.transport.websocket.dispatcher import Connection, TransportServices

logger = logging.getLogger(__name__)

CLOSE_POLICY_VIOLATION = 1008
CLOSE_INVALID_PAYLOAD = 1007


async def serve(socket: Any, services: TransportServices, verifier: CredentialVerifier) -> None:
    try:
        principal = await authenticate(verifier, socket.headers)
    except NetraError:
        await socket.close(code=CLOSE_POLICY_VIOLATION)
        return

    await socket.accept()
    connection = Connection(
        services=services,
        principal=principal,
        send_text_raw=socket.send_text,
        send_bytes_raw=socket.send_bytes,
    )
    try:
        while True:
            message = await socket.receive()
            kind = message.get("type")
            if kind == "websocket.disconnect":
                break
            if message.get("bytes") is not None:
                try:
                    await connection.handle_bytes(message["bytes"])
                except UnreadableMicrophoneFrame:
                    await socket.close(code=CLOSE_INVALID_PAYLOAD)
                    break
                continue
            text = message.get("text")
            if text is None:
                continue
            try:
                await connection.handle_text(text)
            except InvalidRequestError:
                await socket.close(code=CLOSE_INVALID_PAYLOAD)
                break
    finally:
        await connection.on_disconnect()
