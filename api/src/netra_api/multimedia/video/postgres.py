"""PostgreSQL read side for video evidence (the M2 half of M3's video tool).

``StoredVideoEvidenceService`` declares the reads it needs as the
``VideoEvidenceStore`` and ``CapabilityFacts`` protocols and takes them as
constructor arguments; until now nothing implemented them, so
``IntegrationDependencies.video_evidence`` stayed None and every video request
was unregistered. These are those implementations.

Every method authorizes against ``auth.account_id`` by joining through
``sources``: a video_id or source_version_id alone is not authority
(CLAUDE.md "IDs alone are not authority"). A row that exists but belongs to
another account is reported as absent (None / empty), never as a denial, so
these reads cannot be used to probe for other accounts' videos.
"""

from __future__ import annotations

from typing import List, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from netra_api.db.models import (SourceRow, SourceVersionRow, VideoAssetRow,
                                 VideoEvidenceCandidateRow, VideoProviderBindingRow)
from netra_api.multimedia.video.models import (EvidenceProvenance, ProviderAssetBinding,
                                               VideoAsset, VideoEvidenceItem, VideoEvidenceKind,
                                               VideoEvidenceReference, VideoSourceKind)
from netra_api.multimedia.video.readiness import AnalysisStage
from netra_api.multimedia.video.stored_service import AnalysisFacts, PlaybackFacts
from netra_api.platform.auth_context import AuthContext


def _asset(row: VideoAssetRow) -> VideoAsset:
    return VideoAsset(video_id=row.video_id, source_id=row.source_id,
                      source_version_id=row.source_version_id, kind=VideoSourceKind(row.kind),
                      external_ref=row.external_ref, duration_ms=row.duration_ms)


class AsyncPostgresVideoEvidenceStore:
    """Account-scoped reads of video assets, provider bindings and evidence."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _authorized_asset(self, auth: AuthContext, **where: object) -> Optional[VideoAssetRow]:
        column, value = next(iter(where.items()))
        row = (await self.session.execute(
            select(VideoAssetRow)
            .join(SourceRow, SourceRow.source_id == VideoAssetRow.source_id)
            .where(getattr(VideoAssetRow, column) == value,
                   SourceRow.account_id == auth.account_id))).scalars().one_or_none()
        return row

    async def asset(self, auth: AuthContext, video_id: UUID) -> Optional[VideoAsset]:
        row = await self._authorized_asset(auth, video_id=video_id)
        return _asset(row) if row is not None else None

    async def asset_for_version(self, auth: AuthContext, source_version_id: UUID) -> Optional[VideoAsset]:
        row = await self._authorized_asset(auth, source_version_id=source_version_id)
        return _asset(row) if row is not None else None

    async def binding(self, video_id: UUID, provider: str) -> Optional[ProviderAssetBinding]:
        # Deliberately unauthorized by signature (the protocol takes no auth):
        # callers reach it only after asset() has authorized the same video_id,
        # and a binding carries no student-visible content of its own.
        row = (await self.session.execute(
            select(VideoProviderBindingRow).where(
                VideoProviderBindingRow.video_id == video_id,
                VideoProviderBindingRow.provider == provider)
            .order_by(VideoProviderBindingRow.created_at.desc()))).scalars().first()
        if row is None:
            return None
        return ProviderAssetBinding(video_id=row.video_id, provider=row.provider,
                                    provider_index_id=row.provider_index_id,
                                    provider_video_id=row.provider_video_id,
                                    model_name=row.model_name, model_version=row.model_version,
                                    bound_at=row.created_at)

    async def items(self, auth: AuthContext, source_version_id: UUID) -> List[VideoEvidenceItem]:
        asset = await self.asset_for_version(auth, source_version_id)
        if asset is None:
            return []
        rows = (await self.session.execute(
            select(VideoEvidenceCandidateRow)
            .where(VideoEvidenceCandidateRow.source_version_id == source_version_id)
            .order_by(VideoEvidenceCandidateRow.start_ms, VideoEvidenceCandidateRow.ordinal))).scalars().all()
        items: List[VideoEvidenceItem] = []
        for row in rows:
            try:
                kind = VideoEvidenceKind(row.kind)
            except ValueError:
                # A provider payload that introduced a fourth kind is not
                # citable; drop it rather than guess what it meant.
                continue
            items.append(VideoEvidenceItem(
                video_evidence_id=row.candidate_id,
                reference=VideoEvidenceReference(
                    # The candidate id IS the evidence id: the resolver
                    # (AsyncPostgresEvidenceResolver) resolves it as DERIVED.
                    evidence_id=str(row.candidate_id), source_version_id=row.source_version_id,
                    locator=row.locator, start_ms=row.start_ms, end_ms=row.end_ms,
                    video_id=row.video_id),
                kind=kind, description=row.description,
                provenance=_provenance(row.provenance)))
        return items


def _provenance(raw: object) -> Optional[EvidenceProvenance]:
    """None for unrecorded origin, which is itself worth seeing (models.py)."""

    if not isinstance(raw, dict):
        return None
    try:
        return EvidenceProvenance.model_validate(raw)
    except Exception:  # noqa: BLE001 - malformed provenance is absent provenance
        return None


class PostgresCapabilityFacts:
    """Playback and analysis facts, each read from the service that owns it.

    readiness.py requires the two verdicts to be established independently:
    a video may be analysable but not playable (embedding disabled) or
    playable but not analysed. Neither is derived from the other here.
    """

    def __init__(self, session: AsyncSession, *, provider: str,
                 embedded_player_available: bool) -> None:
        self.session = session
        self._provider = provider
        self._player = embedded_player_available

    async def playback(self, auth: AuthContext, asset: VideoAsset) -> PlaybackFacts:
        version = (await self.session.execute(
            select(SourceVersionRow)
            .join(SourceRow, SourceRow.source_id == SourceVersionRow.source_id)
            .where(SourceVersionRow.source_version_id == asset.source_version_id,
                   SourceRow.account_id == auth.account_id))).scalars().one_or_none()
        youtube = asset.kind is VideoSourceKind.YOUTUBE
        return PlaybackFacts(
            authorized=version is not None,
            media_present=asset.external_ref is not None,
            # Embeddability is a fact about the YouTube video that only a
            # player probe establishes; unknown stays None rather than True.
            embeddable=None if youtube else True,
            container_supported=None if youtube else True,
            embedded_player_available=self._player,
        )

    async def analysis(self, auth: AuthContext, asset: VideoAsset) -> AnalysisFacts:
        binding = (await self.session.execute(
            select(VideoProviderBindingRow).where(
                VideoProviderBindingRow.video_id == asset.video_id,
                VideoProviderBindingRow.provider == self._provider))).scalars().first()
        # A binding is the provider's own record that the media is indexed;
        # no binding means nothing has been started for this provider. There
        # is no stage beyond INDEXED, so derived evidence does not raise it.
        stage = AnalysisStage.NOT_STARTED if binding is None else AnalysisStage.INDEXED
        return AnalysisFacts(authorized=True, stage=stage)
