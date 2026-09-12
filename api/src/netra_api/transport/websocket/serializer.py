"""Pydantic mirror of shared/contracts/protocol/v1/*.schema.json.

This module must stay field-for-field identical to the JSON Schema
contracts (CLAUDE.md "Persistence and protocol": "Python and C# models
must conform to shared/contracts/"). Any contract change goes through
shared/contracts/ first, then here and in the C# records under
client/src/Netra.Desktop/Protocol/Dto/, together.

Every model sets extra="forbid" to match the schemas' "additionalProperties":
false. Without it Python would silently accept a message the schema
rejects, and the two sides would drift in opposite directions.

This module is pure shape and validation. It opens no socket, holds no
session state and makes no routing decision; the WebSocket endpoint and
dispatcher own those.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Optional, Union
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from netra_api.platform.errors import UnsupportedProtocolVersionError
from netra_api.session.commands import NavigationCommandName
from netra_api.session.modes import NavigationUnit

PROTOCOL_VERSION = "1.0"
"""Both protocol schemas pin protocol_version to this constant."""


ClientMessageType = Literal[
    "session.resume",
    "turn.submit",
    "navigation.command",
    "response.cancel",
    "playback.ack",
]

ServerMessageType = Literal[
    "session.snapshot",
    "response.segment",
    "quiz.question",
    "error",
]

InputMode = Literal["keyboard", "voice"]
CancelReason = Literal["user_stop", "new_turn", "navigation", "client_shutdown"]
PlaybackAckStatus = Literal["started", "progress", "completed"]
ResponseSegmentKind = Literal["explanation", "hint", "question", "correction"]


class _StrictModel(BaseModel):
    """Base for every wire model: unknown fields are a protocol error."""

    model_config = ConfigDict(extra="forbid")


class SessionResumePayload(_StrictModel):
    last_known_session_version: int = Field(ge=0)
    last_acknowledged_sentence_id: Optional[str] = None


class TurnSubmitPayload(_StrictModel):
    utterance: str = Field(min_length=1, max_length=8000)
    input_mode: InputMode
    transcript_status: Literal["final"] = "final"
    """The schema pins this to the constant "final". An interim transcript
    therefore cannot be submitted as a turn at all — the restriction is
    structural, not a check someone has to remember to write (CLAUDE.md:
    "Interim ASR transcripts never trigger deterministic commands or
    create turns")."""
    expected_session_version: int = Field(ge=0)
    client_timestamp: Optional[datetime] = None


class NavigationCommandPayload(_StrictModel):
    command: NavigationCommandName
    navigation_unit: Optional[NavigationUnit] = None
    expected_session_version: int = Field(ge=0)


class ResponseCancelPayload(_StrictModel):
    cancel_request_id: UUID
    generation_id: Optional[str] = None
    reason: Optional[CancelReason] = None


class PlaybackAckPayload(_StrictModel):
    generation_id: str
    segment_id: str
    sentence_id: str
    status: PlaybackAckStatus
    played_ms: Optional[int] = Field(default=None, ge=0)


ClientPayload = Union[
    SessionResumePayload,
    TurnSubmitPayload,
    NavigationCommandPayload,
    ResponseCancelPayload,
    PlaybackAckPayload,
]

_PAYLOAD_BY_TYPE: dict[str, type[BaseModel]] = {
    "session.resume": SessionResumePayload,
    "turn.submit": TurnSubmitPayload,
    "navigation.command": NavigationCommandPayload,
    "response.cancel": ResponseCancelPayload,
    "playback.ack": PlaybackAckPayload,
}


class ClientToServerMessage(_StrictModel):
    """Mirrors shared/contracts/protocol/v1/client_to_server.schema.json."""

    protocol_version: Literal["1.0"] = PROTOCOL_VERSION
    message_id: UUID
    session_id: UUID
    request_id: UUID
    sequence: Annotated[int, Field(ge=0)]
    type: ClientMessageType
    payload: dict


class ResponseSegmentPayload(_StrictModel):
    """Mirrors the ResponseSegment $def in server_to_client.schema.json."""

    generation_id: str
    segment_id: str
    sentence_id: str
    kind: Optional[ResponseSegmentKind] = None
    text: str = Field(max_length=8000)
    final: bool
    evidence_ids: Optional[list[str]] = None


class ServerToClientMessage(_StrictModel):
    """Mirrors shared/contracts/protocol/v1/server_to_client.schema.json.

    payload stays an untyped mapping because only response.segment has a
    sub-schema so far. Do not add a typed model for session.snapshot,
    quiz.question or error here before the contract defines one; that
    would make this module the de facto contract.
    """

    protocol_version: Literal["1.0"] = PROTOCOL_VERSION
    message_id: UUID
    session_id: UUID
    request_id: UUID
    sequence: Annotated[int, Field(ge=0)]
    type: ServerMessageType
    payload: dict


def parse_client_message(raw: dict) -> tuple[ClientToServerMessage, ClientPayload]:
    """Validate one inbound frame and its payload against the contract.

    Returns the envelope and the payload model matching its type. Raises
    netra_api.platform.errors.UnsupportedProtocolVersionError for an
    unknown protocol_version, and pydantic.ValidationError for anything
    that does not conform.

    The version check runs before payload validation so an unsupported
    version is reported as such rather than as a field error.
    """

    declared_version = raw.get("protocol_version")
    if declared_version != PROTOCOL_VERSION:
        raise UnsupportedProtocolVersionError(
            f"unsupported protocol_version {declared_version!r}; this server speaks {PROTOCOL_VERSION!r}"
        )

    envelope = ClientToServerMessage.model_validate(raw)
    payload_model = _PAYLOAD_BY_TYPE[envelope.type]
    payload = payload_model.model_validate(envelope.payload)
    return envelope, payload  # type: ignore[return-value]
