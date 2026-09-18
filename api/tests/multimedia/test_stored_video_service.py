"""StoredVideoEvidenceService over TEST-ONLY M2/M5 fact doubles.

The store, facts and resolver here are in-memory stand-ins for M2's
unpublished repositories and M5's player facts. They exercise M3's
composition and fail-closed rules, not persistence or real playback.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from fixtures.ohm_law import (
    LECTURE_DURATION_MS,
    SOURCE_VERSION_ID,
    VIDEO_ID,
    lecture_asset,
    transcript_evidence,
    visual_evidence,
)
from fixtures.provider_fakes import FakeGateway, StatusError, local_tracer, settings
from netra_api.content.retrieval.evidence import Evidence, EvidenceRejectionReason, EvidenceResolution, EvidenceTrust
from netra_api.multimedia.providers.twelve_labs_client import MarengoSearchAdapter, RawSearchHit
from netra_api.multimedia.video.evidence_resolution import EvidenceSufficiency
from netra_api.multimedia.video.models import ProviderAssetBinding
from netra_api.multimedia.video.readiness import AnalysisStage, AnalysisUnreadyReason, PlaybackUnavailableReason
from netra_api.multimedia.video.stored_service import AnalysisFacts, PlaybackFacts, StoredVideoEvidenceService
from netra_api.platform.auth_context import AuthContext
from netra_api.platform.errors import ResourceUnavailableError

AUTH = AuthContext(account_id=uuid4(), session_id=uuid4(), request_id=uuid4(), issued_at=datetime(2026, 9, 18, tzinfo=timezone.utc))
NOW = datetime(2026, 9, 18, tzinfo=timezone.utc)


class Resolver:
    """Authorizes every id except those listed as revoked."""

    def __init__(self, revoked=()):
        self.revoked = set(revoked)

    def resolve(self, auth, evidence_ids, pinned_source_version_id=None):
        out = []
        for evidence_id in evidence_ids:
            if evidence_id in self.revoked:
                out.append(EvidenceResolution(evidence_id=evidence_id, rejection_reason=EvidenceRejectionReason.NOT_FOUND))
            else:
                out.append(
                    EvidenceResolution(
                        evidence_id=evidence_id,
                        evidence=Evidence(
                            evidence_id=evidence_id, source_version_id=pinned_source_version_id, locator="l", text="t",
                            provenance="p", trust=EvidenceTrust.DERIVED,
                        ),
                    )
                )
        return out


class Store:
    def __init__(self, items, binding=True):
        self._items = items
        self._binding = (
            ProviderAssetBinding(
                video_id=VIDEO_ID, provider="twelvelabs", provider_index_id="idx-test", provider_video_id="ia-1",
                model_name="marengo-test-model", model_version="test-1", bound_at=NOW,
            )
            if binding
            else None
        )

    async def asset(self, auth, video_id):
        return lecture_asset() if video_id == VIDEO_ID else None

    async def asset_for_version(self, auth, source_version_id):
        return lecture_asset() if source_version_id == SOURCE_VERSION_ID else None

    async def binding(self, video_id, provider):
        return self._binding

    async def items(self, auth, source_version_id):
        return list(self._items)


class Facts:
    def __init__(self, playback=None, analysis=None):
        self._playback = playback or PlaybackFacts(authorized=True, media_present=True, embeddable=None, container_supported=True, embedded_player_available=False)
        self._analysis = analysis or AnalysisFacts(authorized=True, stage=AnalysisStage.INDEXED)

    async def playback(self, auth, asset):
        return self._playback

    async def analysis(self, auth, asset):
        return self._analysis


def _service(items, *, gateway=None, resolver=None, binding=True, facts=None, tracer=None):
    marengo = MarengoSearchAdapter(gateway, settings()) if gateway is not None else None
    return StoredVideoEvidenceService(
        Store(items, binding=binding), resolver or Resolver(), facts or Facts(), provider="twelvelabs",
        moment_before_ms=5_000, moment_after_ms=5_000, search_limit=5, search_timeout_seconds=2, marengo=marengo,
        tracer=tracer, clock=lambda: NOW,
    )


async def test_search_returns_only_stored_items_under_bound_video_hits():
    gateway = FakeGateway(hits=[RawSearchHit("ia-1", 45_000, 50_000, rank=1), RawSearchHit("ia-other", 0, 90_000, rank=0)])
    items = [transcript_evidence(), visual_evidence()]
    found = await _service(items, gateway=gateway).search_evidence(AUTH, SOURCE_VERSION_ID, "what are the axes")
    assert {item.kind.value for item in found} == {"transcript_segment", "visual_description"}


async def test_search_without_binding_or_marengo_returns_nothing_rather_than_everything():
    items = [transcript_evidence(), visual_evidence()]
    assert await _service(items, gateway=FakeGateway(hits=[RawSearchHit("ia-1", 0, 90_000)]), binding=False).search_evidence(AUTH, SOURCE_VERSION_ID, "q") == []
    assert await _service(items).search_evidence(AUTH, SOURCE_VERSION_ID, "q") == []


async def test_search_outage_is_a_safe_unavailable_error():
    service = _service([visual_evidence()], gateway=FakeGateway(fail_with={"search": StatusError(503)}))
    with pytest.raises(ResourceUnavailableError):
        await service.search_evidence(AUTH, SOURCE_VERSION_ID, "q")


async def test_revoked_evidence_is_never_returned():
    visual = visual_evidence()
    service = _service([transcript_evidence(), visual], resolver=Resolver(revoked={visual.reference.evidence_id}))
    items = await service.list_evidence(AUTH, SOURCE_VERSION_ID)
    assert visual not in items and len(items) == 1


async def test_moment_at_captured_time_distinguishes_transcript_only_from_visual():
    service = _service([transcript_evidence(), visual_evidence()])
    assert (await service.evidence_at_player_time(AUTH, VIDEO_ID, 48_000)).sufficiency is EvidenceSufficiency.VISUAL
    transcript_only = _service([transcript_evidence()])
    assert (await transcript_only.evidence_at_player_time(AUTH, VIDEO_ID, 48_000)).sufficiency is EvidenceSufficiency.TRANSCRIPT_ONLY
    assert (await service.evidence_at_player_time(AUTH, VIDEO_ID, 10_000)).sufficiency is EvidenceSufficiency.NO_EVIDENCE_AT_TIME


async def test_captured_time_outside_the_video_or_unknown_video_fails_closed():
    service = _service([visual_evidence()])
    with pytest.raises(ValueError):
        await service.evidence_at_player_time(AUTH, VIDEO_ID, LECTURE_DURATION_MS + 1)
    with pytest.raises(ResourceUnavailableError):
        await service.evidence_at_player_time(AUTH, uuid4(), 1_000)


async def test_playback_and_analysis_are_independent_verdicts():
    playing_but_unanalysed = _service(
        [transcript_evidence()], facts=Facts(analysis=AnalysisFacts(authorized=True, stage=AnalysisStage.INDEXING))
    )
    report = await playing_but_unanalysed.capability_report(AUTH, VIDEO_ID)
    assert report.playback.available is True
    assert report.analysis.ready is False and report.analysis.reason is AnalysisUnreadyReason.INDEXING_IN_PROGRESS

    analysable_not_playable = _service(
        [transcript_evidence(), visual_evidence()],
        facts=Facts(playback=PlaybackFacts(authorized=True, media_present=True, embeddable=None, container_supported=False, embedded_player_available=False)),
    )
    report = await analysable_not_playable.capability_report(AUTH, VIDEO_ID)
    assert report.playback.available is False and report.playback.reason is PlaybackUnavailableReason.UNSUPPORTED_CONTAINER
    assert report.analysis.ready is True and report.analysis.supports_visual_evidence is True


async def test_transcript_only_video_is_analysed_but_not_visually_ready():
    report = await _service([transcript_evidence()]).capability_report(AUTH, VIDEO_ID)
    assert report.analysis.supports_visual_evidence is False


async def test_moment_span_correlates_source_and_outcome_without_text():
    tracer, exporter = local_tracer()
    await _service([transcript_evidence()], tracer=tracer).evidence_at_player_time(AUTH, VIDEO_ID, 48_000)
    tracer.shutdown(1)
    (span,) = [s for s in exporter.spans if s.name == "media.video.moment"]
    assert span.attributes["netra.source_version_id"] == str(SOURCE_VERSION_ID)
    assert span.attributes["netra.outcome"] == "transcript_only"
    assert span.attributes["netra.gap"] == "transcript_only"
    assert transcript_evidence().description not in repr(exporter.spans)
