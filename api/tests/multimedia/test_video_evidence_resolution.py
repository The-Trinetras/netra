"""Resolving evidence around a captured player time.

The case this exists for: at 00:48 the transcript says "this line" and
never names the axes. M1 has to be able to tell that apart from having
real visual evidence, which is what EvidenceSufficiency does.
"""

from uuid import uuid4

import pytest

from fixtures.ohm_law import (
    LECTURE_DURATION_MS,
    QUESTION_TIME_MS,
    VIDEO_ID,
    transcript_evidence,
    visual_evidence,
)
from netra_api.multimedia.video.evidence_resolution import (
    EvidenceSufficiency,
    has_visual_evidence,
    resolve_moment_evidence,
)
from netra_api.multimedia.video.models import VideoEvidenceKind
from netra_api.multimedia.video.timestamps import (
    TimeWindow,
    format_timestamp,
    reference_in_window,
    window_around,
)


def _question_window() -> TimeWindow:
    return window_around(
        QUESTION_TIME_MS, before_ms=6_000, after_ms=10_000, duration_ms=LECTURE_DURATION_MS
    )


def test_the_agentspec_window_covers_the_lecture_moment():
    window = _question_window()

    assert (window.start_ms, window.end_ms) == (42_000, 58_000)
    assert window.contains(QUESTION_TIME_MS)


def test_a_window_is_clamped_to_the_end_of_the_media():
    window = window_around(
        89_000, before_ms=5_000, after_ms=10_000, duration_ms=LECTURE_DURATION_MS
    )

    assert window.end_ms == LECTURE_DURATION_MS


def test_a_window_near_the_start_does_not_go_negative():
    window = window_around(1_000, before_ms=5_000, after_ms=5_000)

    assert window.start_ms == 0


def test_window_around_rejects_a_negative_player_time():
    with pytest.raises(ValueError):
        window_around(-1, before_ms=1_000, after_ms=1_000)


def test_transcript_alone_is_reported_as_an_evidence_gap():
    """Step 3 of the walkthrough: the transcript omits the axes."""

    moment = resolve_moment_evidence(
        [transcript_evidence()], _question_window(), video_id=VIDEO_ID
    )

    assert moment.sufficiency is EvidenceSufficiency.TRANSCRIPT_ONLY
    assert moment.supports_visual_claim is False
    assert moment.transcript_items
    assert moment.visual_items == []


def test_the_transcript_gap_is_statable_to_the_student():
    moment = resolve_moment_evidence(
        [transcript_evidence()], _question_window(), video_id=VIDEO_ID
    )

    statement = moment.gap_statement()
    assert "not an analysis of what is on screen" in statement
    assert format_timestamp(42_000) in statement


def test_visual_evidence_supports_a_claim_about_what_is_shown():
    """Step 4: the retrieved visual result identifies the axes."""

    moment = resolve_moment_evidence(
        [transcript_evidence(), visual_evidence()], _question_window(), video_id=VIDEO_ID
    )

    assert moment.sufficiency is EvidenceSufficiency.VISUAL
    assert moment.supports_visual_claim is True
    assert moment.gap_statement() is None


def test_a_wrong_timestamp_finds_no_evidence_at_that_moment():
    """Distinct from having no evidence at all: the video was processed."""

    far_away = TimeWindow(start_ms=5_000, end_ms=8_000)

    moment = resolve_moment_evidence(
        [transcript_evidence(), visual_evidence()], far_away, video_id=VIDEO_ID
    )

    assert moment.sufficiency is EvidenceSufficiency.NO_EVIDENCE_AT_TIME
    assert "no analysis of this lecture around" in moment.gap_statement()


def test_an_unprocessed_video_reports_no_evidence_at_all():
    moment = resolve_moment_evidence([], _question_window(), video_id=VIDEO_ID)

    assert moment.sufficiency is EvidenceSufficiency.NO_EVIDENCE
    assert "has not been analysed" in moment.gap_statement()


def test_evidence_from_another_video_is_not_evidence_about_this_one():
    """Two lectures can share a clock range and share nothing else."""

    other_video = visual_evidence()
    other_video = other_video.model_copy(
        update={
            "reference": other_video.reference.model_copy(
                update={"video_id": uuid4()}
            )
        }
    )

    moment = resolve_moment_evidence(
        [other_video], _question_window(), video_id=VIDEO_ID
    )

    assert moment.visual_items == []
    assert moment.sufficiency is EvidenceSufficiency.NO_EVIDENCE


def test_a_scene_summary_does_not_establish_what_is_on_screen():
    """It summarises a stretch of video; it does not report a moment."""

    summary = visual_evidence().model_copy(
        update={"kind": VideoEvidenceKind.SCENE_SUMMARY}
    )

    moment = resolve_moment_evidence([summary], _question_window(), video_id=VIDEO_ID)

    assert moment.sufficiency is EvidenceSufficiency.TRANSCRIPT_ONLY
    assert moment.other_items == [summary]
    assert moment.visual_items == []


def test_partially_overlapping_evidence_still_covers_the_moment():
    """Requiring containment would discard a description that does cover it."""

    overlapping = TimeWindow(start_ms=50_000, end_ms=51_000)

    assert reference_in_window(visual_evidence().reference, overlapping) is True


def test_has_visual_evidence_counts_items_not_processing_stage():
    assert has_visual_evidence([transcript_evidence()]) is False
    assert has_visual_evidence([transcript_evidence(), visual_evidence()]) is True


def test_kind_classification_is_explicit_about_visual_support():
    assert VideoEvidenceKind.VISUAL_DESCRIPTION.supports_visual_claim is True
    assert VideoEvidenceKind.TRANSCRIPT_SEGMENT.supports_visual_claim is False
    assert VideoEvidenceKind.SCENE_SUMMARY.supports_visual_claim is False
