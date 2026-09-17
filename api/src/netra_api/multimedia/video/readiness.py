"""Playback availability and analysis readiness — two independent checks.

current-scope.md: "Playback readiness and analysis readiness are
separate per selected video ... A URL, successful playback or
transcript-only access does not establish visual understanding."
multimedia.md repeats it: "Playback permission/availability and
analysis permission/ingestibility must be checked separately for each
selected upload/YouTube video."

The failure this prevents is concrete. A YouTube lecture plays fine in
an embedded player, so a naive check calls the video "ready" — and then
Netra answers a question about what the lecturer is pointing at, having
never processed a single frame. Keeping the two verdicts in separate
types means no caller can obtain one and read it as the other; there is
no field to confuse, because VideoCapabilityReport has no aggregate
"ready" flag and deliberately never will.

Each verdict names its reason. "Unavailable" with no reason is the same
as silence to a student deciding whether to wait or move on.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, model_validator

from netra_api.multimedia.video.models import (
    ProviderAssetBinding,
    VideoAsset,
    VideoSourceKind,
)


class PlaybackUnavailableReason(str, Enum):
    """Why a selected video cannot be played in Netra's own player."""

    NOT_CHECKED = "not_checked"
    ACCESS_DENIED = "access_denied"
    """The student is not authorized for this source."""
    EMBEDDING_NOT_PERMITTED = "embedding_not_permitted"
    """A YouTube video whose owner disallows embedded playback."""
    UNSUPPORTED_CONTAINER = "unsupported_container"
    """An uploaded file the player cannot decode."""
    MEDIA_MISSING = "media_missing"
    """The stored object or the remote video is gone."""
    PLAYER_DEPENDENCY_PENDING = "player_dependency_pending"
    """The embedded-player dependency is still a proposal.

    current-scope.md: "The AgentSpec's embedded YouTube player requires a
    desktop web-view dependency decision and M3/M5 integration tests. It
    is a proposal, not an installed capability." Reported as a distinct
    reason rather than folded into UNSUPPORTED_CONTAINER, because it is a
    pending decision rather than a property of the media — the student is
    told the feature is not built, not that their video is broken."""


class AnalysisUnreadyReason(str, Enum):
    """Why a selected video cannot yet back visual evidence."""

    NOT_CHECKED = "not_checked"
    ACCESS_DENIED = "access_denied"
    PROVIDER_REJECTED_MEDIA = "provider_rejected_media"
    """The provider refused to ingest it: wrong codec, too long, no video track."""
    NOT_INDEXED = "not_indexed"
    """No ProviderAssetBinding exists yet; indexing has not run."""
    INDEXING_IN_PROGRESS = "indexing_in_progress"
    PROCESSING_FAILED = "processing_failed"
    TRANSCRIPT_ONLY = "transcript_only"
    """Words are available, frames are not. The reason that matters most:
    it is the one a caller is most tempted to treat as success."""
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    """A configured provider could not be reached. Distinct from
    PROCESSING_FAILED: nothing about this video is known to be wrong."""


class PlaybackAvailability(BaseModel):
    """Whether the student can play this video inside Netra, and why not."""

    video_id: UUID
    available: bool
    reason: Optional[PlaybackUnavailableReason] = None
    checked_at: Optional[datetime] = None

    @model_validator(mode="after")
    def _check_reason(self) -> "PlaybackAvailability":
        if not self.available and self.reason is None:
            raise ValueError("an unavailable playback verdict must name a reason")
        if self.available and self.reason is not None:
            raise ValueError("an available playback verdict must not carry a reason")
        if self.available and self.checked_at is None:
            raise ValueError("an available playback verdict must record when it was checked")
        return self


class AnalysisReadiness(BaseModel):
    """Whether derived visual evidence exists for this video, and why not.

    supports_visual_evidence is separate from ready on purpose. A video
    can be fully processed and still not support visual claims, because
    only a transcript came back. Collapsing the two would make
    TRANSCRIPT_ONLY unrepresentable, which is the exact confusion this
    module exists to prevent.
    """

    video_id: UUID
    ready: bool
    supports_visual_evidence: bool = False
    reason: Optional[AnalysisUnreadyReason] = None
    binding: Optional[ProviderAssetBinding] = None
    """The provider/model identity backing a ready verdict, so a caller
    can record what produced the evidence it is about to cite."""
    checked_at: Optional[datetime] = None

    @model_validator(mode="after")
    def _check_reason(self) -> "AnalysisReadiness":
        if not self.ready and self.reason is None:
            raise ValueError("an unready analysis verdict must name a reason")
        if self.ready and self.reason not in (None, AnalysisUnreadyReason.TRANSCRIPT_ONLY):
            raise ValueError(
                "a ready analysis verdict may only carry the transcript-only reason"
            )
        if self.supports_visual_evidence and not self.ready:
            raise ValueError("analysis cannot support visual evidence while it is unready")
        if self.supports_visual_evidence and self.reason is AnalysisUnreadyReason.TRANSCRIPT_ONLY:
            raise ValueError("transcript-only analysis cannot support visual evidence")
        if self.ready and self.binding is None:
            raise ValueError("a ready analysis verdict must name the provider binding")
        return self


class VideoCapabilityReport(BaseModel):
    """Both verdicts for one video, kept apart.

    There is no combined "ready" property here by design. Any caller
    that wants one has to decide which capability it actually needs, and
    say so.
    """

    video_id: UUID
    playback: PlaybackAvailability
    analysis: AnalysisReadiness

    @model_validator(mode="after")
    def _check_same_video(self) -> "VideoCapabilityReport":
        if self.playback.video_id != self.video_id or self.analysis.video_id != self.video_id:
            raise ValueError("both verdicts must describe the same video_id")
        return self

    def student_summary(self) -> str:
        """What Netra may honestly tell the student about this video.

        Kept here rather than in the client so that every surface says
        the same thing, and so "plays but cannot be analysed" is never
        rounded to "ready".
        """

        if self.playback.available and self.analysis.supports_visual_evidence:
            return "This lecture can be played and its visuals can be explained."
        if self.playback.available and self.analysis.ready:
            return (
                "This lecture can be played, and its spoken words are available. "
                "Its visuals have not been analysed, so Netra cannot describe what is shown."
            )
        if self.playback.available:
            return (
                "This lecture can be played, but it has not been analysed, "
                "so Netra cannot answer questions about what it shows."
            )
        if self.analysis.supports_visual_evidence:
            return (
                "This lecture cannot be played in Netra, but its visuals have been "
                "analysed and can be explained."
            )
        return "This lecture cannot be played or analysed in Netra."


def assess_playback(
    asset: VideoAsset,
    *,
    authorized: bool,
    checked_at: datetime,
    embeddable: Optional[bool] = None,
    container_supported: Optional[bool] = None,
    media_present: bool = True,
    embedded_player_available: bool = False,
) -> PlaybackAvailability:
    """Decide playback availability from separately established facts.

    Every input is a fact somebody established: authorization from the
    content service, embeddability from discovery metadata, container
    support from the client's player. This function does not go and find
    them out, and it never infers one from another — a video being
    authorized says nothing about whether it will decode.

    embedded_player_available defaults to False because the embedded
    web-view dependency has not been approved. That default is what
    makes the honest answer the automatic one.
    """

    def unavailable(reason: PlaybackUnavailableReason) -> PlaybackAvailability:
        return PlaybackAvailability(
            video_id=asset.video_id, available=False, reason=reason, checked_at=checked_at
        )

    if not authorized:
        return unavailable(PlaybackUnavailableReason.ACCESS_DENIED)
    if not media_present:
        return unavailable(PlaybackUnavailableReason.MEDIA_MISSING)

    if asset.kind is VideoSourceKind.YOUTUBE:
        if embeddable is False:
            return unavailable(PlaybackUnavailableReason.EMBEDDING_NOT_PERMITTED)
        if not embedded_player_available:
            return unavailable(PlaybackUnavailableReason.PLAYER_DEPENDENCY_PENDING)
        if embeddable is None:
            # Embeddability was never checked. Unknown is not permission.
            return unavailable(PlaybackUnavailableReason.NOT_CHECKED)
        return PlaybackAvailability(
            video_id=asset.video_id, available=True, checked_at=checked_at
        )

    if container_supported is None:
        return unavailable(PlaybackUnavailableReason.NOT_CHECKED)
    if not container_supported:
        return unavailable(PlaybackUnavailableReason.UNSUPPORTED_CONTAINER)
    return PlaybackAvailability(video_id=asset.video_id, available=True, checked_at=checked_at)


class AnalysisStage(str, Enum):
    """How far provider processing has got for one video."""

    NOT_STARTED = "not_started"
    INDEXING = "indexing"
    INDEXED = "indexed"
    REJECTED = "rejected"
    FAILED = "failed"
    PROVIDER_UNAVAILABLE = "provider_unavailable"


def assess_analysis(
    asset: VideoAsset,
    *,
    authorized: bool,
    checked_at: datetime,
    stage: AnalysisStage,
    binding: Optional[ProviderAssetBinding] = None,
    has_visual_evidence: bool = False,
) -> AnalysisReadiness:
    """Decide analysis readiness from separately established facts.

    has_visual_evidence must come from counting stored evidence items
    whose kind supports a visual claim — see
    netra_api.multimedia.video.evidence_resolution.has_visual_evidence.
    A finished indexing run does not imply it: a provider can index a
    video, return only a transcript, and leave nothing that establishes
    what is on screen.
    """

    def unready(reason: AnalysisUnreadyReason) -> AnalysisReadiness:
        return AnalysisReadiness(
            video_id=asset.video_id, ready=False, reason=reason, checked_at=checked_at
        )

    if not authorized:
        return unready(AnalysisUnreadyReason.ACCESS_DENIED)
    if stage is AnalysisStage.NOT_STARTED:
        return unready(AnalysisUnreadyReason.NOT_INDEXED)
    if stage is AnalysisStage.INDEXING:
        return unready(AnalysisUnreadyReason.INDEXING_IN_PROGRESS)
    if stage is AnalysisStage.REJECTED:
        return unready(AnalysisUnreadyReason.PROVIDER_REJECTED_MEDIA)
    if stage is AnalysisStage.FAILED:
        return unready(AnalysisUnreadyReason.PROCESSING_FAILED)
    if stage is AnalysisStage.PROVIDER_UNAVAILABLE:
        return unready(AnalysisUnreadyReason.PROVIDER_UNAVAILABLE)

    if binding is None or binding.video_id != asset.video_id:
        # Indexed according to the stage, but nothing records which
        # provider copy or model produced it. Evidence that cannot be
        # attributed cannot be published as validated (multimedia.md).
        return unready(AnalysisUnreadyReason.NOT_INDEXED)

    if not has_visual_evidence:
        return AnalysisReadiness(
            video_id=asset.video_id,
            ready=True,
            supports_visual_evidence=False,
            reason=AnalysisUnreadyReason.TRANSCRIPT_ONLY,
            binding=binding,
            checked_at=checked_at,
        )
    return AnalysisReadiness(
        video_id=asset.video_id,
        ready=True,
        supports_visual_evidence=True,
        binding=binding,
        checked_at=checked_at,
    )
