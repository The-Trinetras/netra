"""Playback availability and analysis readiness stay independent.

current-scope.md: "Playback readiness and analysis readiness are
separate per selected video ... A URL, successful playback or
transcript-only access does not establish visual understanding."

The tests that matter most here are the ones where the two verdicts
disagree, because that is the case a single "ready" flag would erase.
"""

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from fixtures.ohm_law import lecture_asset
from netra_api.multimedia.video.models import ProviderAssetBinding, VideoSourceKind
from netra_api.multimedia.video.readiness import (
    AnalysisReadiness,
    AnalysisStage,
    AnalysisUnreadyReason,
    PlaybackAvailability,
    PlaybackUnavailableReason,
    VideoCapabilityReport,
    assess_analysis,
    assess_playback,
)

CHECKED_AT = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)


def _binding(video_id) -> ProviderAssetBinding:
    return ProviderAssetBinding(
        video_id=video_id,
        provider="twelvelabs",
        provider_index_id="idx_test",
        provider_video_id="tlv_test",
        model_name="pegasus",
        model_version="test-fixture",
        bound_at=CHECKED_AT,
    )


def _youtube_asset():
    return lecture_asset().model_copy(
        update={"kind": VideoSourceKind.YOUTUBE, "external_ref": "abcd1234"}
    )


def test_an_upload_plays_when_authorized_and_the_container_is_supported():
    verdict = assess_playback(
        lecture_asset(), authorized=True, checked_at=CHECKED_AT, container_supported=True
    )

    assert verdict.available is True
    assert verdict.reason is None


def test_an_unauthorized_video_never_plays():
    verdict = assess_playback(
        lecture_asset(), authorized=False, checked_at=CHECKED_AT, container_supported=True
    )

    assert verdict.available is False
    assert verdict.reason is PlaybackUnavailableReason.ACCESS_DENIED


def test_an_unchecked_container_is_not_permission():
    verdict = assess_playback(lecture_asset(), authorized=True, checked_at=CHECKED_AT)

    assert verdict.available is False
    assert verdict.reason is PlaybackUnavailableReason.NOT_CHECKED


def test_a_youtube_video_defaults_to_the_pending_player_dependency():
    """The embedded web-view dependency is a proposal, not an installed capability."""

    verdict = assess_playback(
        _youtube_asset(), authorized=True, checked_at=CHECKED_AT, embeddable=True
    )

    assert verdict.available is False
    assert verdict.reason is PlaybackUnavailableReason.PLAYER_DEPENDENCY_PENDING


def test_a_non_embeddable_youtube_video_is_rejected_before_the_dependency():
    verdict = assess_playback(
        _youtube_asset(),
        authorized=True,
        checked_at=CHECKED_AT,
        embeddable=False,
        embedded_player_available=True,
    )

    assert verdict.reason is PlaybackUnavailableReason.EMBEDDING_NOT_PERMITTED


def test_unknown_embeddability_is_not_permission():
    verdict = assess_playback(
        _youtube_asset(),
        authorized=True,
        checked_at=CHECKED_AT,
        embeddable=None,
        embedded_player_available=True,
    )

    assert verdict.reason is PlaybackUnavailableReason.NOT_CHECKED


def test_a_youtube_video_plays_once_embeddable_and_the_player_exists():
    verdict = assess_playback(
        _youtube_asset(),
        authorized=True,
        checked_at=CHECKED_AT,
        embeddable=True,
        embedded_player_available=True,
    )

    assert verdict.available is True


def test_a_rejected_video_is_never_analysis_ready():
    asset = lecture_asset()

    verdict = assess_analysis(
        asset, authorized=True, checked_at=CHECKED_AT, stage=AnalysisStage.REJECTED
    )

    assert verdict.ready is False
    assert verdict.reason is AnalysisUnreadyReason.PROVIDER_REJECTED_MEDIA


def test_an_unindexed_video_reports_not_indexed():
    asset = lecture_asset()

    verdict = assess_analysis(
        asset, authorized=True, checked_at=CHECKED_AT, stage=AnalysisStage.NOT_STARTED
    )

    assert verdict.reason is AnalysisUnreadyReason.NOT_INDEXED


def test_an_indexed_video_with_no_visual_evidence_is_transcript_only():
    """Processing finished. Nothing came back about what is on screen."""

    asset = lecture_asset()

    verdict = assess_analysis(
        asset,
        authorized=True,
        checked_at=CHECKED_AT,
        stage=AnalysisStage.INDEXED,
        binding=_binding(asset.video_id),
        has_visual_evidence=False,
    )

    assert verdict.ready is True
    assert verdict.supports_visual_evidence is False
    assert verdict.reason is AnalysisUnreadyReason.TRANSCRIPT_ONLY


def test_an_indexed_video_with_visual_evidence_supports_visual_claims():
    asset = lecture_asset()

    verdict = assess_analysis(
        asset,
        authorized=True,
        checked_at=CHECKED_AT,
        stage=AnalysisStage.INDEXED,
        binding=_binding(asset.video_id),
        has_visual_evidence=True,
    )

    assert verdict.supports_visual_evidence is True
    assert verdict.binding.provider_video_id == "tlv_test"


def test_indexed_without_a_binding_is_not_ready():
    """Evidence that cannot be attributed to a model cannot be published."""

    asset = lecture_asset()

    verdict = assess_analysis(
        asset,
        authorized=True,
        checked_at=CHECKED_AT,
        stage=AnalysisStage.INDEXED,
        binding=None,
        has_visual_evidence=True,
    )

    assert verdict.ready is False
    assert verdict.reason is AnalysisUnreadyReason.NOT_INDEXED


def test_a_binding_for_another_video_is_not_accepted():
    asset = lecture_asset()

    verdict = assess_analysis(
        asset,
        authorized=True,
        checked_at=CHECKED_AT,
        stage=AnalysisStage.INDEXED,
        binding=_binding(uuid4()),
        has_visual_evidence=True,
    )

    assert verdict.ready is False


def test_a_provider_outage_is_distinct_from_a_processing_failure():
    asset = lecture_asset()

    outage = assess_analysis(
        asset,
        authorized=True,
        checked_at=CHECKED_AT,
        stage=AnalysisStage.PROVIDER_UNAVAILABLE,
    )
    failure = assess_analysis(
        asset, authorized=True, checked_at=CHECKED_AT, stage=AnalysisStage.FAILED
    )

    assert outage.reason is AnalysisUnreadyReason.PROVIDER_UNAVAILABLE
    assert failure.reason is AnalysisUnreadyReason.PROCESSING_FAILED


def test_playback_success_does_not_make_analysis_ready():
    """The failure this whole module exists to prevent."""

    asset = lecture_asset()
    report = VideoCapabilityReport(
        video_id=asset.video_id,
        playback=assess_playback(
            asset, authorized=True, checked_at=CHECKED_AT, container_supported=True
        ),
        analysis=assess_analysis(
            asset, authorized=True, checked_at=CHECKED_AT, stage=AnalysisStage.NOT_STARTED
        ),
    )

    assert report.playback.available is True
    assert report.analysis.supports_visual_evidence is False
    assert "cannot answer questions about what it shows" in report.student_summary()


def test_a_transcript_only_video_is_not_summarised_as_ready():
    asset = lecture_asset()
    report = VideoCapabilityReport(
        video_id=asset.video_id,
        playback=assess_playback(
            asset, authorized=True, checked_at=CHECKED_AT, container_supported=True
        ),
        analysis=assess_analysis(
            asset,
            authorized=True,
            checked_at=CHECKED_AT,
            stage=AnalysisStage.INDEXED,
            binding=_binding(asset.video_id),
            has_visual_evidence=False,
        ),
    )

    summary = report.student_summary()
    assert "spoken words are available" in summary
    assert "cannot describe what is shown" in summary


def test_the_capability_report_has_no_combined_ready_flag():
    """A caller must say which capability it needs."""

    asset = lecture_asset()
    report = VideoCapabilityReport(
        video_id=asset.video_id,
        playback=assess_playback(
            asset, authorized=True, checked_at=CHECKED_AT, container_supported=True
        ),
        analysis=assess_analysis(
            asset, authorized=True, checked_at=CHECKED_AT, stage=AnalysisStage.NOT_STARTED
        ),
    )

    assert not hasattr(report, "ready")
    assert not hasattr(report, "available")


def test_an_unavailable_verdict_must_name_a_reason():
    with pytest.raises(ValidationError):
        PlaybackAvailability(video_id=uuid4(), available=False)


def test_a_transcript_only_verdict_cannot_claim_visual_support():
    with pytest.raises(ValidationError):
        AnalysisReadiness(
            video_id=uuid4(),
            ready=True,
            supports_visual_evidence=True,
            reason=AnalysisUnreadyReason.TRANSCRIPT_ONLY,
            binding=_binding(uuid4()),
            checked_at=CHECKED_AT,
        )


def test_a_report_rejects_verdicts_about_different_videos():
    asset = lecture_asset()

    with pytest.raises(ValidationError):
        VideoCapabilityReport(
            video_id=asset.video_id,
            playback=assess_playback(
                asset, authorized=True, checked_at=CHECKED_AT, container_supported=True
            ),
            analysis=assess_analysis(
                asset.model_copy(update={"video_id": uuid4()}),
                authorized=True,
                checked_at=CHECKED_AT,
                stage=AnalysisStage.NOT_STARTED,
            ),
        )
