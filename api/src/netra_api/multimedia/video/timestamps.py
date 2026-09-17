"""Timestamp/time-range helpers for video evidence.

Pure, deterministic arithmetic over VideoEvidenceReference time ranges —
no provider or database call, so this is implemented in full rather
than stubbed (mirrors netra_worker.runtime.retries).
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, model_validator

from netra_api.multimedia.video.models import VideoEvidenceReference


def duration_ms(reference: VideoEvidenceReference) -> int:
    """Length of reference's time range, in milliseconds."""

    if reference.end_ms < reference.start_ms:
        raise ValueError("end_ms must be >= start_ms")
    return reference.end_ms - reference.start_ms


def overlaps(a: VideoEvidenceReference, b: VideoEvidenceReference) -> bool:
    """True if a and b's time ranges intersect within the same source video."""

    if a.source_version_id != b.source_version_id or a.locator != b.locator:
        return False
    return a.start_ms < b.end_ms and b.start_ms < a.end_ms


class TimeWindow(BaseModel):
    """A closed-open millisecond interval within one video.

    Used for "the moment the student asked about": the AgentSpec pauses
    at 00:48 and expects evidence covering roughly 00:42-00:58, so a
    single instant is never enough to resolve evidence against.
    """

    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def _check_range(self) -> "TimeWindow":
        if self.end_ms < self.start_ms:
            raise ValueError("end_ms must be >= start_ms")
        return self

    def contains(self, ms: int) -> bool:
        return self.start_ms <= ms < self.end_ms

    def intersects(self, start_ms: int, end_ms: int) -> bool:
        return self.start_ms < end_ms and start_ms < self.end_ms


def window_around(
    captured_player_time_ms: int,
    *,
    before_ms: int,
    after_ms: int,
    duration_ms: Optional[int] = None,
) -> TimeWindow:
    """Build the evidence window around a player time M5 actually captured.

    captured_player_time_ms is a required argument with no default on
    purpose. current-scope.md: "Pause-and-describe keeps the lecture
    paused, records its actual position" and "Do not observe unrelated
    browser tabs" — the position must be reported by the client that owns
    the player, never guessed from elapsed wall-clock time or from where
    Netra last sent audio.

    The window is clamped to the media when duration_ms is known, so a
    question asked in the last seconds of a lecture does not produce a
    range extending past its end.
    """

    if captured_player_time_ms < 0:
        raise ValueError("captured_player_time_ms must be >= 0")
    if before_ms < 0 or after_ms < 0:
        raise ValueError("before_ms and after_ms must be >= 0")

    start_ms = max(0, captured_player_time_ms - before_ms)
    end_ms = captured_player_time_ms + after_ms
    if duration_ms is not None:
        if duration_ms < 0:
            raise ValueError("duration_ms must be >= 0")
        end_ms = min(end_ms, duration_ms)
        start_ms = min(start_ms, end_ms)
    return TimeWindow(start_ms=start_ms, end_ms=end_ms)


def reference_in_window(reference: VideoEvidenceReference, window: TimeWindow) -> bool:
    """Whether a reference's range overlaps window at all.

    Overlap rather than containment: a visual description covering
    00:40-01:00 does cover the moment at 00:48, and requiring
    containment would discard it.
    """

    return window.intersects(reference.start_ms, reference.end_ms)


def format_timestamp(ms: int) -> str:
    """Render milliseconds as H:MM:SS for accessible citation text."""

    if ms < 0:
        raise ValueError("ms must be >= 0")
    total_seconds = ms // 1000
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}"
