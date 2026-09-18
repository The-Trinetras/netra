"""Speech recognition boundary: only an accepted final transcript becomes a turn.

CLAUDE.md: "Only accepted final ASR submits turns"; coordinator.md: "Never
execute a command from an interim ASR transcript." The protocol already pins
turn.submit.transcript_status to "final", so an interim transcript cannot be
submitted over the wire. This gate applies the same rule wherever a
recognition adapter produces events server-side.

Microphone (client-to-server) audio upload has no committed contract
(audio_frame_header.schema.json is server-to-client only), so no server-side
recognition stream is wired to the transport. The Deepgram adapter stays
unavailable until that protocol is approved with M5.
"""

from __future__ import annotations

from typing import AsyncIterator, Optional, Protocol

from pydantic import BaseModel, ConfigDict, Field

from netra_api.transport.websocket.serializer import TurnSubmitPayload


class TranscriptEvent(BaseModel):
    """Provider-neutral recognition event produced by an adapter."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(max_length=8000)
    is_final: bool
    """True only when the adapter reports the utterance as finalized."""


class RecognitionAdapter(Protocol):
    def transcripts(self) -> AsyncIterator[TranscriptEvent]:
        ...


def accept_final_transcript(event: TranscriptEvent) -> Optional[str]:
    """Return the utterance text for a final, non-empty transcript; otherwise None.

    Interim events never return text, so they can neither submit a turn nor
    match a deterministic command.
    """

    if not event.is_final:
        return None
    text = event.text.strip()
    return text or None


def turn_from_final_transcript(event: TranscriptEvent, expected_session_version: int) -> Optional[TurnSubmitPayload]:
    """Build a voice turn.submit payload from an accepted final transcript only."""

    text = accept_final_transcript(event)
    if text is None:
        return None
    return TurnSubmitPayload(
        utterance=text,
        input_mode="voice",
        transcript_status="final",
        expected_session_version=expected_session_version,
    )
