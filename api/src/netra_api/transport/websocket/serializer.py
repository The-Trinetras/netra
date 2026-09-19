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

from netra_api.platform.errors import NetraError, UnsupportedProtocolVersionError, error_code_for, is_retryable
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
    "asr.start",
]

ServerMessageType = Literal[
    "session.snapshot",
    "response.segment",
    "quiz.question",
    "error",
    "asr.transcript",
]

InputMode = Literal["keyboard", "voice"]
CancelReason = Literal["user_stop", "new_turn", "navigation", "client_shutdown"]
PlaybackAckStatus = Literal["started", "progress", "completed"]
ResponseSegmentKind = Literal["explanation", "hint", "question", "correction"]
SnapshotInteractionMode = Literal["idle", "reading", "tutor_lesson", "quiz"]
QuizQuestionKind = Literal["multiple_choice", "true_false", "short_answer", "free_response"]
ErrorCodeLiteral = Literal[
    "AUTH_REQUIRED",
    "AUTHORIZATION_DENIED",
    "SESSION_VERSION_CONFLICT",
    "REQUEST_ID_CONFLICT",
    "INVALID_REQUEST",
    "UNSUPPORTED_PROTOCOL_VERSION",
    "STALE_REQUEST",
    "RESOURCE_UNAVAILABLE",
    "PROVIDER_UNAVAILABLE",
    "INTERNAL_ERROR",
]


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


class AsrStartPayload(_StrictModel):
    """Starts one push-to-talk capture (D-MIC); audio follows as microphone frames."""

    capture_id: UUID


ClientPayload = Union[
    SessionResumePayload,
    TurnSubmitPayload,
    NavigationCommandPayload,
    ResponseCancelPayload,
    PlaybackAckPayload,
    AsrStartPayload,
]

_PAYLOAD_BY_TYPE: dict[str, type[BaseModel]] = {
    "session.resume": SessionResumePayload,
    "turn.submit": TurnSubmitPayload,
    "navigation.command": NavigationCommandPayload,
    "response.cancel": ResponseCancelPayload,
    "playback.ack": PlaybackAckPayload,
    "asr.start": AsrStartPayload,
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


class ActiveLessonRefPayload(_StrictModel):
    lesson_id: UUID


class PendingQuestionRefPayload(_StrictModel):
    """Field-for-field identical to the agent-contract PendingQuestion shape
    (coordinator/handoff.py's PendingQuestion), by design: see the $comment
    on server_to_client.schema.json's PendingQuestionRef $def."""

    question_id: str
    question_version: int = Field(ge=1)
    hints_used: int = Field(ge=0)


class ResultSetRefPayload(_StrictModel):
    result_set_id: UUID
    created_at: datetime


class SessionSnapshotPayload(_StrictModel):
    """Mirrors the SessionSnapshot $def in server_to_client.schema.json.

    Reference-only: pending_question and last_result_set never carry
    their full content here (see the schema's $comment for why). Every
    field is present with an explicit null when the value legitimately
    does not exist — Optional[...] with no default forces callers to say
    so rather than omit the key.
    """

    session_version: int = Field(ge=0)
    interaction_mode: SnapshotInteractionMode
    active_source_version_id: Optional[str]
    """Null for a session that has never opened a source yet (interaction_mode idle)."""
    current_block_id: Optional[str]
    current_sentence_id: Optional[str]
    last_acknowledged_sentence_id: Optional[str]
    active_lesson: Optional[ActiveLessonRefPayload]
    pending_question: Optional[PendingQuestionRefPayload]
    last_result_set: Optional[ResultSetRefPayload]


class QuestionOptionPayload(_StrictModel):
    option_id: str
    text: str


class QuizQuestionPayload(_StrictModel):
    """Mirrors the QuizQuestion $def in server_to_client.schema.json.

    Wire form of netra_api.learning.quiz.models.StudentFacingQuestion,
    which structurally cannot carry an answer_key, rubric or grading
    notes.
    """

    question_id: str
    question_version: int = Field(ge=1)
    kind: QuizQuestionKind
    prompt: str = Field(max_length=2000)
    options: list[QuestionOptionPayload] = Field(default_factory=list)
    hints_used: int = Field(default=0, ge=0)


class ErrorPayload(_StrictModel):
    """Mirrors shared/contracts/protocol/v1/error.schema.json.

    No request_id field here on purpose: the envelope already carries
    request_id at the top level. message must be safe/accessible wording
    only, never exception text — callers build this from
    netra_api.platform.errors.error_code_for(), not from str(exc).
    """

    code: ErrorCodeLiteral
    message: str = Field(max_length=500)
    retryable: bool
    current_session_version: Optional[int] = Field(default=None, ge=0)
    correlation_id: Optional[str] = None
    details: Optional[dict] = None


class AsrTranscriptPayload(_StrictModel):
    """Recognition result for one capture (D-MIC). Only a final one may become a
    turn, and the client submits it; empty final text means nothing was heard."""

    capture_id: UUID
    transcript_id: UUID
    text: str = Field(max_length=8000)
    is_final: bool


ServerPayload = Union[
    SessionSnapshotPayload,
    ResponseSegmentPayload,
    QuizQuestionPayload,
    ErrorPayload,
    AsrTranscriptPayload,
]

_SERVER_PAYLOAD_BY_TYPE: dict[str, type[BaseModel]] = {
    "session.snapshot": SessionSnapshotPayload,
    "response.segment": ResponseSegmentPayload,
    "quiz.question": QuizQuestionPayload,
    "error": ErrorPayload,
    "asr.transcript": AsrTranscriptPayload,
}


class ServerToClientMessage(_StrictModel):
    """Mirrors shared/contracts/protocol/v1/server_to_client.schema.json.

    All server message types are typed (session.snapshot,
    response.segment, quiz.question, error and asr.transcript). payload stays a plain
    dict on the envelope itself; use build_server_message()/the
    _SERVER_PAYLOAD_BY_TYPE registry to validate one against its type,
    the same pattern parse_client_message() uses for inbound frames.
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


def validate_server_payload(message_type: ServerMessageType, payload: dict) -> ServerPayload:
    """Validate an outbound payload against its type's typed model.

    Symmetric to parse_client_message's inbound validation: a handler
    that builds session.snapshot/quiz.question/error content passes it
    through here before serialization, so a field this module doesn't
    know about (or a wrong type) is caught before it reaches the wire,
    the same way extra="forbid" catches it on the way in.
    """

    payload_model = _SERVER_PAYLOAD_BY_TYPE[message_type]
    return payload_model.model_validate(payload)  # type: ignore[return-value]


def error_payload_for(exc: NetraError, *, current_session_version: Optional[int] = None) -> ErrorPayload:
    """Build a safe wire ErrorPayload from a NetraError.

    The code comes from error_code_for(), which switches only on
    exception type, so nothing exception-specific — a message string, a
    stack trace, a database detail — can leak into the wire code. The
    human-readable `message` here is deliberately generic per code
    rather than str(exc): str(exc) on these exceptions already avoids
    leaking internals today, but this function does not depend on that
    staying true for every future NetraError subclass.
    """

    code = error_code_for(exc)
    return ErrorPayload(
        code=code,  # type: ignore[arg-type]
        message=_SAFE_MESSAGE_BY_CODE.get(code, "Something went wrong. Please try again."),
        retryable=is_retryable(code),
        current_session_version=current_session_version,
    )


_SAFE_MESSAGE_BY_CODE: dict[str, str] = {
    "AUTH_REQUIRED": "Please sign in again to continue.",
    "AUTHORIZATION_DENIED": "You don't have access to that.",
    "SESSION_VERSION_CONFLICT": "Your view of this session is out of date. Reconnecting to refresh it.",
    "REQUEST_ID_CONFLICT": "That request could not be processed. Please try again.",
    "INVALID_REQUEST": "That request wasn't understood.",
    "UNSUPPORTED_PROTOCOL_VERSION": "Please update the app to continue.",
    "STALE_REQUEST": "That response is no longer available.",
    "RESOURCE_UNAVAILABLE": "That's temporarily unavailable. Please try again shortly.",
    "PROVIDER_UNAVAILABLE": "A service Netra depends on is temporarily unavailable.",
    "INTERNAL_ERROR": "Something went wrong. Please try again.",
}
