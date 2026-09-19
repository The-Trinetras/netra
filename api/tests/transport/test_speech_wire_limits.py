"""Speech wire details (INT-11b/c): the audio/mpeg media type and the frame size limit."""

import json
import struct
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from netra_api.platform.auth_context import AuthContext
from netra_api.speech.playback_metadata import GenerationRegistry
from netra_api.speech.providers.elevenlabs import media_type_for
from netra_api.speech.quota import InMemoryQuotaLedger
from netra_api.speech.synthesis import InMemoryAudioCache, SpeechOutput, SynthesisConfig
from netra_api.transport.audio.frame import (
    MAX_AUDIO_BYTES_PER_FRAME,
    MAX_FRAME_BYTES,
    MAX_HEADER_BYTES,
    AudioFrameError,
    AudioFrameHeader,
    decode_audio_frame,
    encode_audio_frame,
)

from ohm_fixture import ACCOUNT, SESSION

SCHEMA = json.loads(
    (Path(__file__).resolve().parents[3] / "shared" / "contracts" / "protocol" / "v1" / "audio_frame_header.schema.json").read_text()
)


def _header(**overrides):
    fields = dict(version=1, generation_id="g", segment_id="s", sequence=0, end_of_segment=False, end_of_generation=False, media_type="audio/mpeg")
    fields.update(overrides)
    return AudioFrameHeader(**fields)


def test_limits_match_the_schema():
    assert SCHEMA["maxHeaderBytes"] == MAX_HEADER_BYTES
    assert SCHEMA["maxAudioBytesPerFrame"] == MAX_AUDIO_BYTES_PER_FRAME == 65_536
    assert MAX_FRAME_BYTES == 81_924


def test_elevenlabs_mp3_is_audio_mpeg_and_other_formats_are_refused():
    assert media_type_for("mp3_44100_128") == "audio/mpeg"
    with pytest.raises(ValueError):
        media_type_for("pcm_16000")


def test_a_full_frame_round_trips():
    audio = b"\x01" * MAX_AUDIO_BYTES_PER_FRAME
    frame = encode_audio_frame(_header(), audio)
    assert len(frame) <= MAX_FRAME_BYTES
    assert decode_audio_frame(frame)[1] == audio


def test_the_sender_refuses_an_oversized_frame():
    with pytest.raises(AudioFrameError):
        encode_audio_frame(_header(), b"\x01" * (MAX_AUDIO_BYTES_PER_FRAME + 1))


def test_the_receiver_rejects_an_oversized_frame():
    header = _header().model_dump_json().encode()
    frame = struct.pack(">I", len(header)) + header + b"\x01" * (MAX_AUDIO_BYTES_PER_FRAME + 1)
    with pytest.raises(AudioFrameError):
        decode_audio_frame(frame)


class _BigChunkSynthesizer:
    config = SynthesisConfig(provider="fixture", model_id="m", voice_id="v", media_type="audio/mpeg")

    def __init__(self, chunks):
        self._chunks = chunks

    async def stream(self, text):
        for chunk in self._chunks:
            yield chunk


async def test_large_provider_chunks_are_split_across_frames_never_truncated():
    big = bytes(range(256)) * 800  # 204,800 bytes: more than three full frames
    small = b"tail"
    registry = GenerationRegistry()
    output = SpeechOutput(_BigChunkSynthesizer([big, b"", small]), InMemoryQuotaLedger(characters_per_account=1_000), InMemoryAudioCache(), registry)
    sent: list[bytes] = []

    async def send(frame):
        sent.append(frame)

    auth = AuthContext(account_id=ACCOUNT, session_id=SESSION, request_id=uuid4(), issued_at=datetime.now(timezone.utc))
    generation = registry.start(SESSION, uuid4())
    assert await output.speak_segment(auth, generation, segment_id="seg-0", text="hello", access_scope="source:v", end_of_generation=True, send_bytes=send)

    decoded = [decode_audio_frame(frame) for frame in sent]
    assert len(decoded) == 5
    assert all(len(audio) <= MAX_AUDIO_BYTES_PER_FRAME for _, audio in decoded)
    assert b"".join(audio for _, audio in decoded) == big + small
    assert [header.sequence for header, _ in decoded] == sorted({header.sequence for header, _ in decoded})
    assert [header.end_of_segment for header, _ in decoded] == [False, False, False, False, True]
    assert decoded[-1][0].end_of_generation
