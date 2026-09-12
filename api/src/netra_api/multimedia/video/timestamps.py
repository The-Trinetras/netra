"""Timestamp/time-range helpers for video evidence.

Pure, deterministic arithmetic over VideoEvidenceReference time ranges —
no provider or database call, so this is implemented in full rather
than stubbed (mirrors netra_worker.runtime.retries).
"""

from __future__ import annotations

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


def format_timestamp(ms: int) -> str:
    """Render milliseconds as H:MM:SS for accessible citation text."""

    if ms < 0:
        raise ValueError("ms must be >= 0")
    total_seconds = ms // 1000
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}"
