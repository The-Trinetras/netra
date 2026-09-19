"""Binary microphone frames: client-to-server push-to-talk audio only (D-MIC).

Mirrors shared/contracts/protocol/v1/microphone_frame_header.schema.json
(decision D-MIC, 20 September 2026). Same wire layout as server audio in
frame.py, but a distinct header: neither framing is ever read as the other.

    [4-byte unsigned big-endian header length]
    [UTF-8 JSON header, matching MicrophoneFrameHeader]
    [16-bit signed little-endian PCM, mono, 16 kHz]

A capture opens with an asr.start text message and ends with a frame whose
end_of_utterance is true. ``CaptureGate`` holds one connection's capture and
applies the contract's rules; the endpoint wires it to the recognition adapter.
"""

from __future__ import annotations

from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from netra_api.platform.errors import InvalidRequestError, NetraError
from netra_api.transport.audio.frame import split_length_prefixed

MICROPHONE_MEDIA_TYPE = "audio/L16;rate=16000"
MAX_HEADER_BYTES = 1024
MAX_AUDIO_BYTES_PER_FRAME = 32_000
"""One second of 16 kHz 16-bit mono audio."""
MAX_AUDIO_BYTES_PER_CAPTURE = 1_920_000
"""Sixty seconds of 16 kHz 16-bit mono audio."""


class UnreadableMicrophoneFrame(NetraError):
    """The header cannot be read, so the frame cannot be tied to a capture.

    The contract closes the connection with 1007 for this; the client
    reconnects and resumes as usual.
    """


class CaptureViolation(InvalidRequestError):
    """A readable frame broke the contract; its capture is abandoned.

    Carries the capture's asr.start request_id so the error message
    correlates with the capture, as the contract requires.
    """

    def __init__(self, message: str, *, request_id: UUID, field: str) -> None:
        self.request_id = request_id
        super().__init__(message, field=field)


class MicrophoneFrameHeader(BaseModel):
    """Mirrors shared/contracts/protocol/v1/microphone_frame_header.schema.json."""

    model_config = ConfigDict(extra="forbid", strict=True)

    version: int = Field(ge=1, le=1)
    capture_id: UUID
    sequence: int = Field(ge=0)
    end_of_utterance: bool
    media_type: Literal["audio/L16;rate=16000"]


def decode_microphone_frame(frame: bytes) -> tuple[MicrophoneFrameHeader, bytes]:
    """Parse one binary message; raise UnreadableMicrophoneFrame if the header can't be read."""

    header_bytes, audio = split_length_prefixed(frame, MAX_HEADER_BYTES, UnreadableMicrophoneFrame)
    try:
        header = MicrophoneFrameHeader.model_validate_json(header_bytes)
    except ValidationError as exc:
        raise UnreadableMicrophoneFrame("invalid microphone frame header") from exc
    return header, audio


class _OpenCapture:
    def __init__(self, capture_id: UUID, request_id: UUID) -> None:
        self.capture_id = capture_id
        self.request_id = request_id
        self.next_sequence = 0
        self.audio_bytes = 0


class CaptureGate:
    """One connection's push-to-talk capture: at most one is open at a time."""

    def __init__(self) -> None:
        self._open: Optional[_OpenCapture] = None
        self._seen: set[UUID] = set()

    @property
    def open_request_id(self) -> Optional[UUID]:
        return self._open.request_id if self._open else None

    def start(self, capture_id: UUID, request_id: UUID) -> Optional[UUID]:
        """Open a capture. Returns the request_id of a capture this one ended
        (it gets no final transcript), or None. A reused capture_id is refused."""

        if capture_id in self._seen:
            raise InvalidRequestError("capture_id was already used on this connection", field="payload.capture_id")
        self._seen.add(capture_id)
        ended = self.open_request_id
        self._open = _OpenCapture(capture_id, request_id)
        return ended

    def abandon(self) -> Optional[UUID]:
        """End the open capture without a final transcript (disconnect, STOP)."""

        ended = self.open_request_id
        self._open = None
        return ended

    def admit(self, header: MicrophoneFrameHeader, audio: bytes) -> Optional[bytes]:
        """Return the frame's audio to forward, or None to drop it silently.

        Frames for an unknown, ended or abandoned capture are dropped: they can
        still be in flight. Any other fault abandons the capture and raises
        CaptureViolation. After an end_of_utterance frame the capture is closed.
        """

        capture = self._open
        if capture is None or header.capture_id != capture.capture_id:
            return None

        def violation(message: str, field: str) -> CaptureViolation:
            self._open = None
            return CaptureViolation(message, request_id=capture.request_id, field=field)

        if header.sequence != capture.next_sequence:
            raise violation("frame sequence is not the next one", "sequence")
        if len(audio) > MAX_AUDIO_BYTES_PER_FRAME:
            raise violation("frame carries more than one second of audio", "audio")
        if len(audio) % 2:
            raise violation("frame audio is not whole 16-bit samples", "audio")
        if capture.audio_bytes + len(audio) > MAX_AUDIO_BYTES_PER_CAPTURE:
            raise violation("capture is longer than sixty seconds", "audio")

        capture.next_sequence += 1
        capture.audio_bytes += len(audio)
        if header.end_of_utterance:
            self._open = None
        return audio
