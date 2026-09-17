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

from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from netra_api.multimedia.evidence import VisualEvidenceReference


class VideoSourceKind(str, Enum):
    """Where a video came from. Both kinds are in current scope."""

    UPLOAD = "upload"
    """A lecture the student uploaded; its bytes live in private object storage."""
    YOUTUBE = "youtube"
    """A video selected from discovery. Netra never stores its bytes."""


class VideoAsset(BaseModel):
    """Netra's canonical identity for one video, independent of any provider.

    multimedia.md: "Keep provider asset/index IDs distinct from
    canonical Netra source IDs." video_id and source_version_id are what
    every evidence item, session and job keys off; a Twelve Labs asset id
    appears only in ProviderAssetBinding. Swapping or re-indexing a
    provider then changes one record instead of invalidating every
    citation a student has been given.
    """

    video_id: UUID
    source_id: UUID
    source_version_id: UUID
    kind: VideoSourceKind
    external_ref: Optional[str] = None
    """The YouTube video id, or the object-storage key for an upload.
    Still not a provider asset id: this identifies the media, not a
    provider's copy of it."""
    duration_ms: Optional[int] = Field(default=None, ge=0)
    """Known media duration, when it has been established. None means
    unknown, which is why time-range checks treat it as unbounded rather
    than as zero."""


class ProviderAssetBinding(BaseModel):
    """Binds one canonical VideoAsset to one provider's copy of it.

    Carries the model identity as well as the asset identity, because
    multimedia.md requires tracking "model/version and provenance for
    reproducible evidence interpretation": evidence produced by one
    Pegasus version is not interchangeable with evidence from another,
    and a re-index that changes the model must not silently reuse old
    descriptions.
    """

    video_id: UUID
    provider: str
    """Adapter name, e.g. "twelvelabs". Never a raw SDK object."""
    provider_index_id: str
    provider_video_id: str
    model_name: str
    model_version: str
    bound_at: datetime


class EvidenceProvenance(BaseModel):
    """How one piece of derived video evidence was produced."""

    provider: str
    model_name: str
    model_version: str
    produced_at: datetime
    stage: str
    """The job stage that produced it, matching the stage names recorded
    in netra_worker.runtime.job_repository.Job.completed_stages, so a
    citation can be traced back to the run that created it."""


class VideoEvidenceKind(str, Enum):
    TRANSCRIPT_SEGMENT = "transcript_segment"
    VISUAL_DESCRIPTION = "visual_description"
    SCENE_SUMMARY = "scene_summary"

    @property
    def supports_visual_claim(self) -> bool:
        """Whether this kind may back a claim about what is *shown*.

        A transcript segment never can. current-scope.md: "A URL,
        successful playback or transcript-only access does not establish
        visual understanding" — the AgentSpec's whole first beat is a
        transcript that says "this line" and never names the axes. A
        scene summary is excluded too: it summarises a stretch of video
        rather than reporting what is visible at a moment, so it cannot
        establish an axis label or a plotted value.
        """

        return self is VideoEvidenceKind.VISUAL_DESCRIPTION


class VideoEvidenceReference(VisualEvidenceReference):
    """Anchors derived video evidence to a start/end time range.

    locator (inherited) identifies which source video within
    source_version_id; start_ms/end_ms narrow the reference to a time
    range within it.
    """

    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    video_id: Optional[UUID] = None
    """Canonical VideoAsset.video_id this evidence belongs to.

    Optional only because locator already identifies the video within a
    source version for evidence created before assets were modelled;
    resolution helpers prefer video_id when it is present, because a
    locator string is easier to get wrong."""

    @model_validator(mode="after")
    def _check_range(self) -> "VideoEvidenceReference":
        if self.end_ms < self.start_ms:
            raise ValueError("end_ms must be >= start_ms")
        return self


class VideoEvidenceItem(BaseModel):
    """One piece of derived, citable evidence about a source video."""

    video_evidence_id: UUID
    reference: VideoEvidenceReference
    kind: VideoEvidenceKind
    description: str
    """Blind-accessible text: a transcript excerpt, a described scene, or
    a summarized answer — never raw pixels."""
    provenance: Optional[EvidenceProvenance] = None
    """Which provider/model/stage produced this item. None for evidence
    whose origin was not recorded, which is itself worth seeing: an
    unprovenanced item cannot be reproduced or re-checked."""

    @property
    def supports_visual_claim(self) -> bool:
        return self.kind.supports_visual_claim


class VideoEvidenceCandidate(BaseModel):
    """Derived video content that is not yet citable evidence.

    A worker job produces these; it cannot produce a VideoEvidenceItem,
    because that requires an evidence_id, and an evidence_id only exists
    once the API service layer has registered the content as DERIVED
    evidence and can authorize it (CLAUDE.md "Data authority and
    security"; see netra_api.multimedia.evidence.resolve_and_authorize).

    Keeping the pre-registration shape in its own type is what makes
    that boundary impossible to skip by accident: there is no field a
    job could fill in to promote its own output.
    """

    video_id: UUID
    source_version_id: UUID
    locator: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    kind: VideoEvidenceKind
    description: str
    provenance: EvidenceProvenance

    @model_validator(mode="after")
    def _check_range(self) -> "VideoEvidenceCandidate":
        if self.end_ms < self.start_ms:
            raise ValueError("end_ms must be >= start_ms")
        return self
