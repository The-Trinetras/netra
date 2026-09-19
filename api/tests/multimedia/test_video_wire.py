"""C8 draft conformance; every value is synthetic."""

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from netra_api.multimedia.video.models import ProviderAssetBinding
from netra_api.multimedia.video.readiness import (
    AnalysisReadiness,
    AnalysisUnreadyReason,
    PlaybackAvailability,
    PlaybackUnavailableReason,
    VideoCapabilityReport,
)
from netra_api.multimedia.video.wire import (
    ActiveVideo,
    AnalysisFact,
    PausedPlayerTime,
    PlaybackFact,
    VideoMoment,
    VideoReadiness,
    public_readiness,
)

CONTRACTS = Path(__file__).resolve().parents[3] / "shared/contracts/video/v1"
MODELS = {
    "paused_player_time": PausedPlayerTime,
    "active_video": ActiveVideo,
    "video_readiness": VideoReadiness,
    "video_moment": VideoMoment,
}
VIDEO = {"video_id": str(UUID(int=1)), "source_version_id": str(UUID(int=2))}
WHEN = datetime(2026, 9, 20, 9, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("name, model", MODELS.items())
def test_committed_schema_matches_executable_payload(name, model):
    schema = json.loads((CONTRACTS / f"{name}.schema.json").read_text())
    for metadata in ("$schema", "$id", "$comment"):
        schema.pop(metadata)
    assert schema == model.model_json_schema()


@pytest.mark.parametrize("name, model", MODELS.items())
def test_review_examples_parse_without_coercion(name, model):
    example = (CONTRACTS / "examples" / f"{name}.json").read_text()
    assert model.model_validate_json(example).model_dump(mode="json") == json.loads(example)


def test_player_time_is_strict_and_inside_the_media():
    PausedPlayerTime.model_validate_json(json.dumps({"video": VIDEO, "position_ms": 48250}))
    for bad in (
        {"video": VIDEO, "position_ms": -1},
        {"video": VIDEO, "position_ms": "48250"},
        {"video": VIDEO, "position_ms": 48.25},
        {"video": VIDEO, "position_ms": 90_001, "duration_ms": 90_000},
        {"video": VIDEO, "position_ms": 1, "wall_clock_ms": 5},
        {"video": {"video_id": str(UUID(int=1))}, "position_ms": 1},
    ):
        with pytest.raises(ValidationError):
            PausedPlayerTime.model_validate_json(json.dumps(bad))


def test_verdicts_cannot_contradict_themselves():
    with pytest.raises(ValidationError):
        PlaybackFact(available=False)
    with pytest.raises(ValidationError):
        PlaybackFact(available=True, reason=PlaybackUnavailableReason.MEDIA_MISSING)
    with pytest.raises(ValidationError):
        AnalysisFact(ready=False, supports_visual_evidence=False)
    with pytest.raises(ValidationError):
        AnalysisFact(ready=True, supports_visual_evidence=True, reason=AnalysisUnreadyReason.TRANSCRIPT_ONLY)
    with pytest.raises(ValidationError):
        AnalysisFact(ready=False, supports_visual_evidence=True, reason=AnalysisUnreadyReason.NOT_INDEXED)
    with pytest.raises(ValidationError):
        VideoMoment.model_validate({"video": VIDEO, "start_ms": 10, "end_ms": 9})


def test_readiness_has_no_aggregate_flag_and_no_provider_identity():
    report = VideoCapabilityReport(
        video_id=UUID(int=1),
        playback=PlaybackAvailability(video_id=UUID(int=1), available=True, checked_at=WHEN),
        analysis=AnalysisReadiness(
            video_id=UUID(int=1),
            ready=True,
            reason=AnalysisUnreadyReason.TRANSCRIPT_ONLY,
            binding=ProviderAssetBinding(
                video_id=UUID(int=1),
                provider="twelvelabs",
                provider_index_id="index-secret-shape",
                provider_video_id="provider-video-7",
                model_name="pegasus",
                model_version="1.5",
                bound_at=WHEN,
            ),
            checked_at=WHEN,
        ),
    )

    public = public_readiness(report, source_version_id=UUID(int=2))
    text = public.model_dump_json()

    assert public.playback.available and public.analysis.ready
    assert not public.analysis.supports_visual_evidence
    assert public.analysis.reason is AnalysisUnreadyReason.TRANSCRIPT_ONLY
    assert public.student_summary == report.student_summary()
    assert "ready" not in VideoReadiness.model_fields
    for private in ("twelvelabs", "index-secret-shape", "provider-video-7", "pegasus", "binding"):
        assert private not in text
    assert VideoReadiness.model_validate_json(text) == public


def test_an_unplayable_but_analysed_video_stays_unplayable():
    report = VideoCapabilityReport(
        video_id=UUID(int=1),
        playback=PlaybackAvailability(
            video_id=UUID(int=1), available=False, reason=PlaybackUnavailableReason.EMBEDDING_NOT_PERMITTED
        ),
        analysis=AnalysisReadiness(
            video_id=UUID(int=1),
            ready=True,
            supports_visual_evidence=True,
            binding=ProviderAssetBinding(
                video_id=UUID(int=1), provider="p", provider_index_id="i", provider_video_id="v",
                model_name="m", model_version="1", bound_at=WHEN,
            ),
            checked_at=WHEN,
        ),
    )

    public = public_readiness(report, source_version_id=UUID(int=2))

    assert not public.playback.available
    assert public.playback.reason is PlaybackUnavailableReason.EMBEDDING_NOT_PERMITTED
    assert public.analysis.supports_visual_evidence
    assert "cannot be played" in public.student_summary
