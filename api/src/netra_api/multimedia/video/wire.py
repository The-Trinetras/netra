"""C8 review draft: public video time and readiness payloads (M5-VIDEO).

Not mounted on a route or added to protocol v1 until Arshad reviews the
session fields (shared/contracts/video/v1/README.md). These payloads carry
facts established elsewhere: the player's own paused time from the client,
and the separate playback and analysis verdicts from ``readiness.py``. They
never combine the two verdicts, never expose a provider binding, and never
authorize anything by themselves.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from netra_api.multimedia.video.readiness import (
    AnalysisUnreadyReason,
    PlaybackUnavailableReason,
    VideoCapabilityReport,
)


class PublicPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class VideoRef(PublicPayload):
    """Netra's canonical identity for one selected video (``VideoAsset``).

    Never a provider asset id; a YouTube id alone is not a Netra video.
    """

    video_id: UUID
    source_version_id: UUID


class PausedPlayerTime(PublicPayload):
    """Where the student's player actually stood when Netra was asked.

    Captured by the client that owns the player, after the pause took
    effect, from the player itself (the IFrame API's getCurrentTime, or the
    local media player's position), rounded down to whole milliseconds.
    Never elapsed wall-clock time and never where Netra last spoke. It is
    the ``captured_player_time_ms`` that ``timestamps.window_around`` needs.
    """

    video: VideoRef
    position_ms: int = Field(ge=0)
    duration_ms: Optional[int] = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _within_media(self) -> "PausedPlayerTime":
        if self.duration_ms is not None and self.position_ms > self.duration_ms:
            raise ValueError("position_ms cannot be past the end of the media")
        return self


class ActiveVideo(PublicPayload):
    """Proposed ``session.snapshot.active_video``: the video being studied
    and the last paused position the Session service recorded for it, so a
    reconnect or restart cues the player at exactly that position."""

    video: VideoRef
    paused_at_ms: Optional[int] = Field(default=None, ge=0)


class PlaybackFact(PublicPayload):
    """The server's playback verdict (access, media present, embeddable as
    far as the server knows). The client's player adds what only it can
    observe, such as an embed refused at load time."""

    available: bool
    reason: Optional[PlaybackUnavailableReason] = None
    checked_at: Optional[AwareDatetime] = None

    @model_validator(mode="after")
    def _reason_matches(self) -> "PlaybackFact":
        if self.available == (self.reason is not None):
            raise ValueError("an unavailable verdict names a reason; an available one does not")
        return self


class AnalysisFact(PublicPayload):
    """The server's analysis verdict. ``transcript_only`` is ready but
    cannot support visual claims; there is no aggregate ready flag."""

    ready: bool
    supports_visual_evidence: bool
    reason: Optional[AnalysisUnreadyReason] = None
    checked_at: Optional[AwareDatetime] = None

    @model_validator(mode="after")
    def _consistent(self) -> "AnalysisFact":
        if not self.ready and self.reason is None:
            raise ValueError("an unready verdict must name a reason")
        if self.ready and self.reason not in (None, AnalysisUnreadyReason.TRANSCRIPT_ONLY):
            raise ValueError("a ready verdict may only carry the transcript-only reason")
        if self.supports_visual_evidence and (not self.ready or self.reason is not None):
            raise ValueError("visual evidence needs a ready, not transcript-only, verdict")
        return self


class VideoReadiness(PublicPayload):
    """Response of the proposed readiness route: both verdicts, apart, and
    the one summary sentence every surface must use."""

    video: VideoRef
    playback: PlaybackFact
    analysis: AnalysisFact
    student_summary: str = Field(min_length=1, max_length=500)


class VideoMoment(PublicPayload):
    """A time range an answer relies on, so the student can jump to it
    (F9). Whether the moment rests on an AI description is carried by the
    evidence payload (C6), not guessed here."""

    video: VideoRef
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def _ordered(self) -> "VideoMoment":
        if self.end_ms < self.start_ms:
            raise ValueError("end_ms must be >= start_ms")
        return self


def public_readiness(report: VideoCapabilityReport, *, source_version_id: UUID) -> VideoReadiness:
    """Project the server's report explicitly; the provider binding and any
    model identity stay server-side."""

    return VideoReadiness(
        video=VideoRef(video_id=report.video_id, source_version_id=source_version_id),
        playback=PlaybackFact(
            available=report.playback.available,
            reason=report.playback.reason,
            checked_at=report.playback.checked_at,
        ),
        analysis=AnalysisFact(
            ready=report.analysis.ready,
            supports_visual_evidence=report.analysis.supports_visual_evidence,
            reason=report.analysis.reason,
            checked_at=report.analysis.checked_at,
        ),
        student_summary=report.student_summary(),
    )
