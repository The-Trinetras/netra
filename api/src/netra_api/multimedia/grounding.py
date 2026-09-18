"""Which multimedia evidence may count as *support* for a claim (D2 input).

M4's optional-check grounding has two halves. Citation binding (M4,
implemented) proves a draft cites evidence this turn authorized. Support
(undecided product policy, P-1) asks whether that evidence actually backs
the claim. This module answers only M3's part of the second question:
before any entailment check runs, is this *kind* of multimedia evidence
eligible to support this *kind* of claim at all?

A valid citation is not support. An authorized, resolvable reference to
a transcript segment is a perfectly valid citation, and it still cannot
support "the x axis is current": transcript-only evidence does not
establish what was shown (current-scope.md). The verdicts here keep those
apart, and every uncertain case fails closed.

This decides no teaching policy and performs no entailment. M4 applies
its own support check (P-1) only to evidence this gate calls ELIGIBLE.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel

from netra_api.multimedia.validation import ValidationReport
from netra_api.multimedia.video.models import VideoEvidenceKind


class ClaimKind(str, Enum):
    """What a draft claims, as far as media evidence is concerned."""

    SPOKEN = "spoken"
    """What the lecturer said."""
    VISUAL = "visual"
    """What is shown: axis labels, plotted shape, slide text, diagram parts."""
    VALUE = "value"
    """An exact value, unit, sign or cell: a table cell, an equation symbol."""


class MediaEvidenceKind(str, Enum):
    TABLE = "table"
    EQUATION = "equation"
    CHART = "chart"
    VIDEO_TRANSCRIPT = "video_transcript"
    VIDEO_VISUAL_DESCRIPTION = "video_visual_description"
    VIDEO_SCENE_SUMMARY = "video_scene_summary"


class SupportEligibility(str, Enum):
    ELIGIBLE = "eligible"
    """May be offered to M4's support check for this claim kind."""
    CITATION_ONLY = "citation_only"
    """A valid citation that cannot support this kind of claim."""
    UNVERIFIED_EXTRACTION = "unverified_extraction"
    """Extraction exists but was not checked against the original, or failed."""
    UNREADABLE = "unreadable"
    """The source itself was unreadable where it matters; a stated gap."""
    PENDING_DECISION = "pending_decision"
    """Eligibility depends on an unmade decision (M1-M3-V). Fails closed."""


class EligibilityVerdict(BaseModel):
    eligibility: SupportEligibility
    reason: str

    @property
    def may_support(self) -> bool:
        return self.eligibility is SupportEligibility.ELIGIBLE


def video_kind(kind: VideoEvidenceKind) -> MediaEvidenceKind:
    return {
        VideoEvidenceKind.TRANSCRIPT_SEGMENT: MediaEvidenceKind.VIDEO_TRANSCRIPT,
        VideoEvidenceKind.VISUAL_DESCRIPTION: MediaEvidenceKind.VIDEO_VISUAL_DESCRIPTION,
        VideoEvidenceKind.SCENE_SUMMARY: MediaEvidenceKind.VIDEO_SCENE_SUMMARY,
    }[kind]


def support_eligibility(
    evidence: MediaEvidenceKind,
    claim: ClaimKind,
    *,
    report: Optional[ValidationReport] = None,
) -> EligibilityVerdict:
    """M3's gate; see the module docstring. ``report`` is required for extractions."""

    if evidence in (MediaEvidenceKind.TABLE, MediaEvidenceKind.EQUATION, MediaEvidenceKind.CHART):
        if claim is ClaimKind.SPOKEN:
            return EligibilityVerdict(eligibility=SupportEligibility.CITATION_ONLY, reason="a document object cannot support what was said in a lecture")
        if report is None or not report.checked_findings:
            return EligibilityVerdict(eligibility=SupportEligibility.UNVERIFIED_EXTRACTION, reason="no check against the original media")
        if report.unreadable:
            return EligibilityVerdict(eligibility=SupportEligibility.UNREADABLE, reason="part of the source was unreadable")
        if not report.is_source_verified:
            return EligibilityVerdict(eligibility=SupportEligibility.UNVERIFIED_EXTRACTION, reason="extraction does not match the original media")
        return EligibilityVerdict(eligibility=SupportEligibility.ELIGIBLE, reason="source-verified extraction")

    if evidence is MediaEvidenceKind.VIDEO_TRANSCRIPT:
        if claim is ClaimKind.SPOKEN:
            return EligibilityVerdict(eligibility=SupportEligibility.ELIGIBLE, reason="observed speech supports what was said")
        return EligibilityVerdict(
            eligibility=SupportEligibility.CITATION_ONLY,
            reason="transcript-only evidence does not establish what was shown or an exact value",
        )

    if evidence is MediaEvidenceKind.VIDEO_VISUAL_DESCRIPTION:
        # Pegasus text over a Netra-chosen interval: the time is observed,
        # the description is GENERATED. Whether that may support a visual
        # claim is the open M1/M3 rule M1-M3-V; until decided, fail closed.
        return EligibilityVerdict(
            eligibility=SupportEligibility.PENDING_DECISION,
            reason="model-generated visual description; support rule M1-M3-V is undecided",
        )

    # Scene summaries span a stretch of video; they never establish a moment's content.
    return EligibilityVerdict(eligibility=SupportEligibility.CITATION_ONLY, reason="a scene summary is not a reading of a moment")
