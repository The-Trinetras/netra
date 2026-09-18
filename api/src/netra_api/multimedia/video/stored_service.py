"""VideoEvidenceService over stored evidence, Marengo retrieval and explicit facts.

Implements netra_api.multimedia.video.service.VideoEvidenceService, which
M1 registers as ``IntegrationDependencies.video_evidence``. Every fact it
needs from another owner arrives through a port, and nothing is assumed
when a port cannot answer:

- ``VideoEvidenceStore`` (M2): the account-authorized canonical asset, its
  provider binding and its registered evidence items. Storage and access
  checks are M2's; this service still re-authorizes every returned item
  through resolve_and_authorize, so an id alone never grants delivery.
- ``CapabilityFacts`` (M2 job stage + M5 player facts): playback and
  analysis facts, fed unchanged into assess_playback / assess_analysis,
  which keep the two verdicts independent.
- ``MarengoSearchAdapter`` (M3, optional): retrieval intervals restricted
  to the bound provider video. Without it, or when the video has no
  binding, search returns an empty list — it never falls back to
  returning everything.

The service never looks up the player position itself:
evidence_at_player_time takes M5's captured time as an argument. The
window around it is explicit configuration (before/after ms), not an M3
policy default.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, List, Optional, Protocol
from uuid import UUID

from netra_api.multimedia.evidence import resolve_and_authorize
from netra_api.multimedia.providers.errors import ProviderError
from netra_api.multimedia.providers.twelve_labs_client import MarengoSearchAdapter
from netra_api.multimedia.tracing import media_span, record_outcome
from netra_api.multimedia.video.evidence_resolution import (
    MomentEvidence,
    has_visual_evidence,
    resolve_moment_evidence,
)
from netra_api.multimedia.video.models import ProviderAssetBinding, VideoAsset, VideoEvidenceItem
from netra_api.multimedia.video.readiness import (
    AnalysisStage,
    VideoCapabilityReport,
    assess_analysis,
    assess_playback,
)
from netra_api.multimedia.video.search import select_evidence_for_hits
from netra_api.multimedia.video.timestamps import window_around
from netra_api.platform.auth_context import AuthContext
from netra_api.platform.errors import NetraError, ResourceUnavailableError
from netra_api.platform.tracing import Tracer


class VideoEvidenceStore(Protocol):
    """M2-owned reads. Every method authorizes against auth; None means not visible."""

    async def asset(self, auth: AuthContext, video_id: UUID) -> Optional[VideoAsset]:
        ...

    async def asset_for_version(self, auth: AuthContext, source_version_id: UUID) -> Optional[VideoAsset]:
        ...

    async def binding(self, video_id: UUID, provider: str) -> Optional[ProviderAssetBinding]:
        ...

    async def items(self, auth: AuthContext, source_version_id: UUID) -> List[VideoEvidenceItem]:
        ...


@dataclass(frozen=True)
class PlaybackFacts:
    authorized: bool
    media_present: bool
    embeddable: Optional[bool]
    container_supported: Optional[bool]
    embedded_player_available: bool


@dataclass(frozen=True)
class AnalysisFacts:
    authorized: bool
    stage: AnalysisStage


class CapabilityFacts(Protocol):
    """Facts owned elsewhere (M2 permission/job stage, M5 player support)."""

    async def playback(self, auth: AuthContext, asset: VideoAsset) -> PlaybackFacts:
        ...

    async def analysis(self, auth: AuthContext, asset: VideoAsset) -> AnalysisFacts:
        ...


class StoredVideoEvidenceService:
    """See module docstring. All collaborators are explicit constructor arguments."""

    def __init__(
        self,
        store: VideoEvidenceStore,
        resolver: Any,
        facts: CapabilityFacts,
        *,
        provider: str,
        moment_before_ms: int,
        moment_after_ms: int,
        search_limit: int,
        search_timeout_seconds: float,
        marengo: Optional[MarengoSearchAdapter] = None,
        tracer: Optional[Tracer] = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        if moment_before_ms < 0 or moment_after_ms < 0 or search_limit <= 0 or search_timeout_seconds <= 0:
            raise ValueError("moment window, search limit and timeout are explicit positive configuration")
        self._store = store
        self._resolver = resolver
        self._facts = facts
        self._provider = provider
        self._before = moment_before_ms
        self._after = moment_after_ms
        self._limit = search_limit
        self._search_timeout = search_timeout_seconds
        self._marengo = marengo
        self._tracer = tracer
        self._clock = clock

    async def _authorized_items(self, auth: AuthContext, source_version_id: UUID) -> List[VideoEvidenceItem]:
        authorized: List[VideoEvidenceItem] = []
        for item in await self._store.items(auth, source_version_id):
            if item.reference.source_version_id != source_version_id:
                continue
            try:
                await resolve_and_authorize(auth, self._resolver, item.reference)
            except NetraError:
                continue
            authorized.append(item)
        return authorized

    async def list_evidence(self, auth: AuthContext, source_version_id: UUID) -> List[VideoEvidenceItem]:
        return await self._authorized_items(auth, source_version_id)

    async def search_evidence(self, auth: AuthContext, source_version_id: UUID, query_text: str) -> List[VideoEvidenceItem]:
        with media_span(self._tracer, "media.video.search", operation="search_lecture", source_version_id=source_version_id) as span:
            asset = await self._store.asset_for_version(auth, source_version_id)
            if asset is None or self._marengo is None:
                record_outcome(span, "no_search", netra_evidence_count=0)
                return []
            binding = await self._store.binding(asset.video_id, self._provider)
            if binding is None:
                record_outcome(span, "not_indexed", netra_evidence_count=0)
                return []
            items = await self._authorized_items(auth, source_version_id)
            try:
                hits = await self._marengo.search_ranges(
                    provider_video_id=binding.provider_video_id,
                    query_text=query_text,
                    duration_ms=asset.duration_ms,
                    timeout_seconds=self._search_timeout,
                    source_version_id=source_version_id,
                )
            except ProviderError as error:
                raise ResourceUnavailableError("video search is unavailable") from error
            selected = select_evidence_for_hits(items, hits, limit=self._limit)
            record_outcome(span, "results" if selected else "no_results", netra_evidence_count=len(selected))
            return selected

    async def capability_report(self, auth: AuthContext, video_id: UUID) -> VideoCapabilityReport:
        asset = await self._store.asset(auth, video_id)
        if asset is None:
            raise ResourceUnavailableError("video is not available")
        now = self._clock()
        playback_facts = await self._facts.playback(auth, asset)
        analysis_facts = await self._facts.analysis(auth, asset)
        binding = await self._store.binding(asset.video_id, self._provider)
        items = await self._authorized_items(auth, asset.source_version_id) if analysis_facts.authorized else []
        return VideoCapabilityReport(
            video_id=asset.video_id,
            playback=assess_playback(
                asset,
                authorized=playback_facts.authorized,
                checked_at=now,
                embeddable=playback_facts.embeddable,
                container_supported=playback_facts.container_supported,
                media_present=playback_facts.media_present,
                embedded_player_available=playback_facts.embedded_player_available,
            ),
            analysis=assess_analysis(
                asset,
                authorized=analysis_facts.authorized,
                checked_at=now,
                stage=analysis_facts.stage,
                binding=binding,
                has_visual_evidence=has_visual_evidence(items),
            ),
        )

    async def evidence_at_player_time(self, auth: AuthContext, video_id: UUID, captured_player_time_ms: int) -> MomentEvidence:
        asset = await self._store.asset(auth, video_id)
        if asset is None:
            raise ResourceUnavailableError("video is not available")
        if captured_player_time_ms < 0 or (asset.duration_ms is not None and captured_player_time_ms > asset.duration_ms):
            raise ValueError("captured player time is outside this video")
        window = window_around(captured_player_time_ms, before_ms=self._before, after_ms=self._after, duration_ms=asset.duration_ms)
        with media_span(
            self._tracer,
            "media.video.moment",
            operation="evidence_at_player_time",
            source_version_id=asset.source_version_id,
            video_id=asset.video_id,
            captured_time_ms=captured_player_time_ms,
        ) as span:
            items = await self._authorized_items(auth, asset.source_version_id)
            moment = resolve_moment_evidence(items, window, video_id=asset.video_id)
            record_outcome(
                span,
                moment.sufficiency.value,
                uncertainty="generated" if moment.visual_items else moment.sufficiency.value,
                netra_evidence_count=len(moment.visual_items) + len(moment.transcript_items),
            )
            return moment
