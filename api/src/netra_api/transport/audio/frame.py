"""Binary audio frame framing: server-to-client synthesized/playback audio only.

Approved 2026-09-12 alongside session.snapshot/quiz.question/error (see
docs/architecture/message-flow.md and
shared/contracts/protocol/v1/audio_frame_header.schema.json, which this
module mirrors field-for-field).

Wire layout of one WebSocket binary message:

    [4-byte unsigned big-endian header length]
    [UTF-8 JSON header, matching AudioFrameHeader]
    [raw audio bytes]

This framing applies ONLY to server -> client synthesized/playback audio.
Microphone (client -> server) audio upload is governed by its own
approved protocol and must never be silently reinterpreted as this
format — nothing here decodes or validates an uploaded microphone frame.

No base64 fallback exists or may be introduced (CLAUDE.md: audio is not
transported as base64 JSON payloads).
"""

from __future__ import annotations

import struct
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from netra_api.platform.errors import NetraError

MAX_HEADER_BYTES = 16 * 1024
"""Structural cap from audio_frame_header.schema.json. No authoritative
total-binary-message size limit exists yet anywhere in the runtime
baseline or committed contracts; that remains an explicit open
transport-hardening decision this module does not invent (see
docs/architecture/message-flow.md's unresolved-decisions table)."""

_LENGTH_PREFIX_SIZE = 4
_SUPPORTED_VERSION = 1


class AudioFrameError(NetraError):
    """Raised for any structurally invalid binary audio frame.

    Deliberately one exception type for every validation failure listed
    in audio_frame_header.schema.json (bad length, oversized header,
    unknown/mistyped fields, unsupported version): the caller's response
    is the same in every case — reject the frame, never guess a
    correction.
    """


class AudioFrameHeader(BaseModel):
    """Mirrors shared/contracts/protocol/v1/audio_frame_header.schema.json."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1, le=1)
    generation_id: str = Field(min_length=1)
    segment_id: str = Field(min_length=1)
    sequence: int = Field(ge=0)
    end_of_segment: bool
    end_of_generation: bool
    media_type: str = Field(min_length=1)


def encode_audio_frame(header: AudioFrameHeader, audio_bytes: bytes) -> bytes:
    """Build one binary WebSocket message from a header and raw audio bytes.

    Raises AudioFrameError if the encoded header would exceed
    MAX_HEADER_BYTES — callers must not silently truncate or fall back to
    a different framing.
    """

    header_bytes = header.model_dump_json().encode("utf-8")
    if len(header_bytes) > MAX_HEADER_BYTES:
        raise AudioFrameError(
            f"encoded header is {len(header_bytes)} bytes, exceeding the {MAX_HEADER_BYTES}-byte limit"
        )

    length_prefix = struct.pack(">I", len(header_bytes))
    return length_prefix + header_bytes + audio_bytes


def decode_audio_frame(frame: bytes) -> tuple[AudioFrameHeader, bytes]:
    """Parse one binary WebSocket message into its header and raw audio bytes.

    Validates the declared header length against the frame's actual
    available bytes BEFORE attempting to parse any JSON — a frame that
    claims a header longer than what is actually present is rejected
    outright, never read past its real end. Raises AudioFrameError for
    every structural problem: missing/short length prefix, zero or
    negative length, a length that does not fit the frame, oversized
    header, malformed JSON, unknown fields, wrong field types, or an
    unsupported version.
    """

    header_bytes, audio_bytes = split_length_prefixed(frame, MAX_HEADER_BYTES, AudioFrameError)

    try:
        header = AudioFrameHeader.model_validate_json(header_bytes)
    except ValidationError as exc:
        raise AudioFrameError(f"invalid audio frame header: {exc}") from exc

    if header.version != _SUPPORTED_VERSION:
        raise AudioFrameError(f"unsupported audio frame version {header.version}")

    return header, audio_bytes


def split_length_prefixed(frame: bytes, max_header_bytes: int, error: type[Exception]) -> tuple[bytes, bytes]:
    """Split [4-byte big-endian length][header][rest], checking the length
    against the bytes actually present before anything is parsed.

    Shared by the server audio and microphone framings, which have the same
    layout but distinct headers; ``error`` is raised for every fault.
    """

    if len(frame) < _LENGTH_PREFIX_SIZE:
        raise error("frame is shorter than the 4-byte length prefix")

    (declared_header_length,) = struct.unpack(">I", frame[:_LENGTH_PREFIX_SIZE])

    if declared_header_length == 0:
        raise error("declared header length is zero")

    if declared_header_length > max_header_bytes:
        raise error(f"declared header length {declared_header_length} exceeds the {max_header_bytes}-byte limit")

    available_after_prefix = len(frame) - _LENGTH_PREFIX_SIZE
    if declared_header_length > available_after_prefix:
        raise error(
            f"declared header length {declared_header_length} exceeds the "
            f"{available_after_prefix} bytes actually available in the frame"
        )

    header_end = _LENGTH_PREFIX_SIZE + declared_header_length
    return frame[_LENGTH_PREFIX_SIZE:header_end], frame[header_end:]


class GenerationSequenceTracker:
    """Per-generation strictly-increasing sequence enforcement.

    Duplicate or decreasing sequence numbers within one generation_id are
    rejected; gaps are permitted, since transport/recovery behavior must
    not assume every frame arrives. One tracker instance is scoped to one
    generation_id — a new generation starts a fresh tracker rather than
    resetting shared state, so an old generation's sequence history can
    never influence admission of a new one.
    """

    def __init__(self, generation_id: str) -> None:
        self._generation_id = generation_id
        self._highest_sequence: Optional[int] = None

    def admit(self, header: AudioFrameHeader) -> bool:
        """Return True if this frame's sequence may be admitted, else False.

        Does not raise: a rejected frame is simply dropped by the caller,
        not treated as a protocol violation the way a malformed header is.
        """

        if header.generation_id != self._generation_id:
            return False

        if self._highest_sequence is not None and header.sequence <= self._highest_sequence:
            return False

        self._highest_sequence = header.sequence
        return True
