"""Resolve video evidence around the player time M5 actually captured.

This module answers one question for M1: given the moment the student
paused at, is there evidence good enough to explain what the lecturer
is pointing at — or only words?

That distinction is the AgentSpec's central beat. At 00:48 the
transcript says "The resistance stays constant" while the lecturer
points at a line whose axes are never spoken. Step 3 of the walkthrough
requires the run to record "axes not established from transcript" and
step 4 requires that recorded gap to be the reason the next action
changes. So the outcome type here is not a boolean: TRANSCRIPT_ONLY is
a distinct, named result that M1 can branch on, and it is what the
integration checklist means by "insufficient transcript".

Nothing here decides whether the student may see a video, and nothing
here calls a provider. It sorts already-stored, already-authorized
evidence against a window.
"""

from __future__ import annotations

from enum import Enum
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from netra_api.multimedia.video.models import (
    VideoEvidenceItem,
    VideoEvidenceKind,
)
from netra_api.multimedia.video.timestamps import (
    TimeWindow,
    format_timestamp,
    reference_in_window,
)


class EvidenceSufficiency(str, Enum):
    """How well the evidence at a moment supports a claim about what is shown."""

    VISUAL = "visual"
    """At least one visual description covers the window. The only
    outcome that may back a statement about what the video shows."""
    TRANSCRIPT_ONLY = "transcript_only"
    """Words cover the window; frames do not. M1 must treat this as an
    evidence gap and change strategy, not as a weaker answer."""
    NO_EVIDENCE_AT_TIME = "no_evidence_at_time"
    """Evidence exists for this video, but none of it covers the window.
    Usually a wrong timestamp: a captured player time from a different
    video, or a stale position from before a seek."""
    NO_EVIDENCE = "no_evidence"
    """Nothing has been derived for this video at all."""


class MomentEvidence(BaseModel):
    """Everything known about one video at one captured moment."""

    video_id: Optional[UUID] = None
    window: TimeWindow
    sufficiency: EvidenceSufficiency
    visual_items: List[VideoEvidenceItem] = Field(default_factory=list)
    transcript_items: List[VideoEvidenceItem] = Field(default_factory=list)
    other_items: List[VideoEvidenceItem] = Field(default_factory=list)
    """Scene summaries and anything else that covers the window without
    establishing what is on screen. Kept so a caller can still read them
    aloud, and separate so none of them can be mistaken for visual
    support."""

    @property
    def supports_visual_claim(self) -> bool:
        return self.sufficiency is EvidenceSufficiency.VISUAL

    def gap_statement(self) -> Optional[str]:
        """A student-safe statement of the limitation, or None when there is none.

        Worded as what Netra could not establish, never as an apology and
        never as a guess. This is the text the AgentSpec's step 4 calls
        for when "the axes remain unreadable": state the limitation
        instead of claiming the comparison succeeded.
        """

        if self.sufficiency is EvidenceSufficiency.VISUAL:
            return None
        at = format_timestamp(self.window.start_ms)
        if self.sufficiency is EvidenceSufficiency.TRANSCRIPT_ONLY:
            return (
                f"At {at} Netra has the lecturer's words but not an analysis of "
                "what is on screen, so it cannot say what the picture shows."
            )
        if self.sufficiency is EvidenceSufficiency.NO_EVIDENCE_AT_TIME:
            return f"Netra has no analysis of this lecture around {at}."
        return "This lecture has not been analysed, so Netra cannot describe it."


def resolve_moment_evidence(
    items: List[VideoEvidenceItem],
    window: TimeWindow,
    *,
    video_id: Optional[UUID] = None,
) -> MomentEvidence:
    """Sort evidence covering window into visual, transcript and other.

    items must already be authorized for the caller's account and pinned
    source version; this function does no access checking (see
    netra_api.multimedia.evidence.resolve_and_authorize, which the video
    service calls first). Passing unauthorized items here would not be
    caught, which is why the service layer, not this one, is the
    boundary.

    video_id, when given, also filters: evidence from a different video
    that happens to overlap the same clock range is not evidence about
    this one.
    """

    for_this_video = [
        item
        for item in items
        if video_id is None or item.reference.video_id in (None, video_id)
    ]
    relevant = [
        item for item in for_this_video if reference_in_window(item.reference, window)
    ]

    visual = [item for item in relevant if item.kind.supports_visual_claim]
    transcript = [
        item for item in relevant if item.kind is VideoEvidenceKind.TRANSCRIPT_SEGMENT
    ]
    other = [
        item
        for item in relevant
        if not item.kind.supports_visual_claim
        and item.kind is not VideoEvidenceKind.TRANSCRIPT_SEGMENT
    ]

    if visual:
        sufficiency = EvidenceSufficiency.VISUAL
    elif transcript or other:
        sufficiency = EvidenceSufficiency.TRANSCRIPT_ONLY
    elif for_this_video:
        # Scoped to this video on purpose: evidence about a different
        # lecture does not make "we have analysed this one, just not at
        # that moment" true.
        sufficiency = EvidenceSufficiency.NO_EVIDENCE_AT_TIME
    else:
        sufficiency = EvidenceSufficiency.NO_EVIDENCE

    return MomentEvidence(
        video_id=video_id,
        window=window,
        sufficiency=sufficiency,
        visual_items=visual,
        transcript_items=transcript,
        other_items=other,
    )


def has_visual_evidence(items: List[VideoEvidenceItem]) -> bool:
    """Whether any stored item for a video can back a visual claim.

    This is the input netra_api.multimedia.video.readiness.assess_analysis
    needs for has_visual_evidence. Counting items rather than trusting a
    processing stage is deliberate: a completed Pegasus run that returned
    only a transcript leaves the stage INDEXED and the video still unable
    to answer a question about what is shown.
    """

    return any(item.kind.supports_visual_claim for item in items)
