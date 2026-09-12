from uuid import uuid4

import pytest

from netra_api.multimedia.video.models import VideoEvidenceReference
from netra_api.multimedia.video.timestamps import duration_ms, format_timestamp, overlaps


def _reference(source_version_id, locator, start_ms, end_ms):
    return VideoEvidenceReference(
        evidence_id="ev-1",
        source_version_id=source_version_id,
        locator=locator,
        start_ms=start_ms,
        end_ms=end_ms,
    )


def test_duration_ms_computes_range_length():
    reference = _reference(uuid4(), "video-1", 1000, 4500)
    assert duration_ms(reference) == 3500


def test_duration_ms_rejects_end_before_start():
    reference = _reference(uuid4(), "video-1", 5000, 1000)
    with pytest.raises(ValueError):
        duration_ms(reference)


def test_overlaps_true_for_intersecting_ranges_in_same_video():
    source_version_id = uuid4()
    a = _reference(source_version_id, "video-1", 0, 5000)
    b = _reference(source_version_id, "video-1", 4000, 9000)
    assert overlaps(a, b) is True


def test_overlaps_false_for_non_intersecting_ranges_in_same_video():
    source_version_id = uuid4()
    a = _reference(source_version_id, "video-1", 0, 1000)
    b = _reference(source_version_id, "video-1", 2000, 3000)
    assert overlaps(a, b) is False


def test_overlaps_false_for_different_videos():
    a = _reference(uuid4(), "video-1", 0, 5000)
    b = _reference(uuid4(), "video-1", 0, 5000)
    assert overlaps(a, b) is False


def test_format_timestamp_renders_h_mm_ss():
    assert format_timestamp(3_723_000) == "1:02:03"


def test_format_timestamp_rejects_negative():
    with pytest.raises(ValueError):
        format_timestamp(-1)
