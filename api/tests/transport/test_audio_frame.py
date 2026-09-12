"""Binary audio frame framing: server-to-client synthesized audio only.

Every structural rule from
shared/contracts/protocol/v1/audio_frame_header.schema.json is exercised
here: header length validated against actual bytes before parsing,
oversized/zero/invalid lengths rejected, unknown fields rejected, wrong
types rejected, unsupported versions rejected, and per-generation
sequence admission (duplicates/decreases rejected, gaps permitted).
"""

import json
import struct

import pytest

from netra_api.transport.audio.frame import (
    MAX_HEADER_BYTES,
    AudioFrameError,
    AudioFrameHeader,
    GenerationSequenceTracker,
    decode_audio_frame,
    encode_audio_frame,
)


def _header(**overrides) -> AudioFrameHeader:
    defaults = dict(
        version=1,
        generation_id="gen-1",
        segment_id="seg-1",
        sequence=0,
        end_of_segment=False,
        end_of_generation=False,
        media_type="audio/mpeg",
    )
    defaults.update(overrides)
    return AudioFrameHeader(**defaults)


def test_round_trips_header_and_audio_bytes():
    audio_bytes = b"\x00\x01\x02\x03"
    frame = encode_audio_frame(_header(sequence=5), audio_bytes)

    header, remaining = decode_audio_frame(frame)

    assert header.sequence == 5
    assert header.generation_id == "gen-1"
    assert remaining == audio_bytes


def test_rejects_frame_shorter_than_length_prefix():
    with pytest.raises(AudioFrameError):
        decode_audio_frame(b"\x00\x00")


def test_rejects_zero_declared_length():
    frame = struct.pack(">I", 0) + b"remaining"
    with pytest.raises(AudioFrameError):
        decode_audio_frame(frame)


def test_rejects_declared_length_exceeding_actual_frame_bytes():
    """The length must be validated against actual available bytes BEFORE
    any attempt to parse — this is the case a malicious or corrupt frame
    would use to make a naive parser read past the buffer."""

    frame = struct.pack(">I", 1000) + b"too short"
    with pytest.raises(AudioFrameError):
        decode_audio_frame(frame)


def test_rejects_header_exceeding_16kib():
    oversized = struct.pack(">I", MAX_HEADER_BYTES + 1) + b"x" * (MAX_HEADER_BYTES + 1)
    with pytest.raises(AudioFrameError):
        decode_audio_frame(oversized)


def test_encode_refuses_to_build_an_oversized_header():
    huge_generation_id = "g" * (MAX_HEADER_BYTES + 100)
    with pytest.raises(AudioFrameError):
        encode_audio_frame(_header(generation_id=huge_generation_id), b"")


def test_rejects_unknown_header_field():
    raw_header = {
        "version": 1,
        "generation_id": "gen-1",
        "segment_id": "seg-1",
        "sequence": 0,
        "end_of_segment": False,
        "end_of_generation": False,
        "media_type": "audio/mpeg",
        "unexpected_field": "value",
    }
    frame = _frame_from_raw_header(raw_header)
    with pytest.raises(AudioFrameError):
        decode_audio_frame(frame)


def test_rejects_wrong_field_type():
    raw_header = {
        "version": 1,
        "generation_id": "gen-1",
        "segment_id": "seg-1",
        "sequence": "not-a-number",
        "end_of_segment": False,
        "end_of_generation": False,
        "media_type": "audio/mpeg",
    }
    frame = _frame_from_raw_header(raw_header)
    with pytest.raises(AudioFrameError):
        decode_audio_frame(frame)


def test_rejects_unsupported_version():
    raw_header = {
        "version": 2,
        "generation_id": "gen-1",
        "segment_id": "seg-1",
        "sequence": 0,
        "end_of_segment": False,
        "end_of_generation": False,
        "media_type": "audio/mpeg",
    }
    frame = _frame_from_raw_header(raw_header)
    with pytest.raises(AudioFrameError):
        decode_audio_frame(frame)


def test_sequence_tracker_admits_strictly_increasing_sequence():
    tracker = GenerationSequenceTracker("gen-1")
    assert tracker.admit(_header(sequence=0)) is True
    assert tracker.admit(_header(sequence=1)) is True


def test_sequence_tracker_permits_gaps():
    tracker = GenerationSequenceTracker("gen-1")
    assert tracker.admit(_header(sequence=0)) is True
    assert tracker.admit(_header(sequence=5)) is True


def test_sequence_tracker_rejects_duplicate_sequence():
    tracker = GenerationSequenceTracker("gen-1")
    assert tracker.admit(_header(sequence=3)) is True
    assert tracker.admit(_header(sequence=3)) is False


def test_sequence_tracker_rejects_decreasing_sequence():
    tracker = GenerationSequenceTracker("gen-1")
    assert tracker.admit(_header(sequence=5)) is True
    assert tracker.admit(_header(sequence=2)) is False


def test_sequence_tracker_rejects_frame_for_a_different_generation():
    """One tracker is scoped to one generation_id; a frame belonging to any
    other generation is never admitted through it."""

    tracker = GenerationSequenceTracker("gen-1")
    assert tracker.admit(_header(generation_id="gen-2", sequence=0)) is False


def _frame_from_raw_header(raw_header: dict) -> bytes:
    header_bytes = json.dumps(raw_header).encode("utf-8")
    return struct.pack(">I", len(header_bytes)) + header_bytes
