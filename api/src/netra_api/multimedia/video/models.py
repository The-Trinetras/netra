"""Video evidence domain models.

Video processing is a bounded tool, not an agent (CLAUDE.md
"Architecture: only two agents"). A VideoEvidenceReference anchors
derived video evidence (a transcript segment, a visual description, a
Pegasus-generated answer) to a time range within a source video, so a
student always knows *where in which video* a claim comes from
(CLAUDE.md "Evidence rules": "Every evidence item keeps ...
locator/page/timestamp where relevant").
"""

from __future__ import annotations

from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field

from netra_api.multimedia.evidence import VisualEvidenceReference


class VideoEvidenceKind(str, Enum):
    TRANSCRIPT_SEGMENT = "transcript_segment"
    VISUAL_DESCRIPTION = "visual_description"
    SCENE_SUMMARY = "scene_summary"


class VideoEvidenceReference(VisualEvidenceReference):
    """Anchors derived video evidence to a start/end time range.

    locator (inherited) identifies which source video within
    source_version_id; start_ms/end_ms narrow the reference to a time
    range within it.
    """

    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)


class VideoEvidenceItem(BaseModel):
    """One piece of derived, citable evidence about a source video."""

    video_evidence_id: UUID
    reference: VideoEvidenceReference
    kind: VideoEvidenceKind
    description: str
    """Blind-accessible text: a transcript excerpt, a described scene, or
    a summarized answer — never raw pixels."""
