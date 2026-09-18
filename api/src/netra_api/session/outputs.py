"""Transport-neutral description of what a handled request delivers.

Session navigation and Coordinator turns both decide *what* to deliver; the
transport decides *how* (message ids, sequence numbers, generation
registration, synthesis). Keeping the plan separate lets the session and
Coordinator be tested without a socket and keeps one delivery path for
eligibility fencing.

Source reading versus generated explanation: the protocol's ResponseSegment
``kind`` has no source-reading value, and inventing one is not permitted.
Plans mark ``origin`` internally; on the wire, source-reading segments omit
``kind`` (it is optional) and generated segments always carry one. That
convention is a PROPOSAL awaiting M5 review (docs/team/handoffs/M1.md).
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from netra_api.speech.playback_metadata import SentenceOrigin

PlaybackControl = Literal["none", "cancel", "pause", "resume"]
SegmentKindName = Literal["explanation", "hint", "question", "correction"]


class PlannedSegment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    origin: SentenceOrigin
    kind: Optional[SegmentKindName] = None
    text: str = Field(min_length=1, max_length=8000)
    evidence_ids: tuple[str, ...] = ()
    source_version_id: Optional[str] = None
    block_id: Optional[str] = None
    sentence_id: Optional[str] = None
    """For source reading this is the canonical source sentence id, so a
    playback acknowledgement maps back to an exact reading position."""


class PlannedQuestion(BaseModel):
    """The quiz.question payload (public fields only) to deliver after segments."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    question_id: str
    question_version: int = Field(ge=1)
    kind: Literal["multiple_choice", "true_false", "short_answer", "free_response"]
    prompt: str = Field(max_length=2000)
    options: tuple[tuple[str, str], ...] = ()
    hints_used: int = Field(default=0, ge=0)


class ResponsePlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    playback: PlaybackControl = "none"
    """Applied to existing output before anything new is delivered."""
    segments: tuple[PlannedSegment, ...] = ()
    speak: bool = True
    """False for text-only notices (e.g. orientation) that must not supersede
    or interrupt current playback."""
    question: Optional[PlannedQuestion] = None
    cancel_turn: bool = False
    """Cancel any in-flight Coordinator turn for this session."""

    @classmethod
    def notice(cls, text: str, *, playback: PlaybackControl = "none", speak: bool = False) -> "ResponsePlan":
        return cls(
            playback=playback,
            speak=speak,
            segments=(PlannedSegment(origin="generated", kind="explanation", text=text),),
        )
