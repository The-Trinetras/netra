"""Microphone protocol (D-MIC): asr.start, microphone frames, asr.transcript.

Checks the committed schemas, examples and Python mirror agree, and that the
frame rules in microphone_frame_header.schema.json are enforced.
"""

import json
import struct
from pathlib import Path
from typing import get_args
from uuid import uuid4

import pytest
from pydantic import ValidationError

from netra_api.platform.errors import InvalidRequestError
from netra_api.transport.audio.frame import AudioFrameError, AudioFrameHeader, decode_audio_frame, encode_audio_frame
from netra_api.transport.audio.microphone import (
    MAX_AUDIO_BYTES_PER_CAPTURE,
    MAX_AUDIO_BYTES_PER_FRAME,
    MAX_HEADER_BYTES,
    MICROPHONE_MEDIA_TYPE,
    CaptureGate,
    CaptureViolation,
    MicrophoneFrameHeader,
    UnreadableMicrophoneFrame,
    decode_microphone_frame,
)
from netra_api.transport.websocket.serializer import (
    AsrStartPayload,
    AsrTranscriptPayload,
    ClientMessageType,
    ServerMessageType,
    parse_client_message,
    validate_server_payload,
)

CONTRACTS = Path(__file__).resolve().parents[3] / "shared" / "contracts"
PROTOCOL = CONTRACTS / "protocol" / "v1"


def _json(path):
    return json.loads(path.read_text())


def _frame(header: dict, audio: bytes = b"") -> bytes:
    header_bytes = json.dumps(header).encode()
    return struct.pack(">I", len(header_bytes)) + header_bytes + audio


def _header(capture, sequence=0, end=False, **overrides) -> dict:
    header = {
        "version": 1,
        "capture_id": str(capture),
        "sequence": sequence,
        "end_of_utterance": end,
        "media_type": MICROPHONE_MEDIA_TYPE,
    }
    header.update(overrides)
    return header


def _decoded(capture, sequence=0, end=False, audio=b""):
    return decode_microphone_frame(_frame(_header(capture, sequence, end), audio))


# ------------------------------------------------------------ schema and models


def test_message_type_enums_match_the_schemas():
    client = _json(PROTOCOL / "client_to_server.schema.json")["properties"]["type"]["enum"]
    server = _json(PROTOCOL / "server_to_client.schema.json")["properties"]["type"]["enum"]
    assert set(client) == set(get_args(ClientMessageType))
    assert set(server) == set(get_args(ServerMessageType))


@pytest.mark.parametrize(
    "schema_file, definition, model",
    [
        ("client_to_server.schema.json", "AsrStart", AsrStartPayload),
        ("server_to_client.schema.json", "AsrTranscript", AsrTranscriptPayload),
        ("microphone_frame_header.schema.json", None, MicrophoneFrameHeader),
    ],
)
def test_models_have_exactly_the_schema_fields(schema_file, definition, model):
    schema = _json(PROTOCOL / schema_file)
    shape = schema["$defs"][definition] if definition else schema
    assert shape["additionalProperties"] is False
    assert set(shape["properties"]) == set(model.model_fields)
    assert set(shape["required"]) == {name for name, field in model.model_fields.items() if field.is_required()}


def test_frame_limits_match_the_schema():
    schema = _json(PROTOCOL / "microphone_frame_header.schema.json")
    assert schema["maxHeaderBytes"] == MAX_HEADER_BYTES
    assert schema["maxAudioBytesPerFrame"] == MAX_AUDIO_BYTES_PER_FRAME
    assert schema["maxAudioBytesPerCapture"] == MAX_AUDIO_BYTES_PER_CAPTURE
    assert schema["properties"]["media_type"]["const"] == MICROPHONE_MEDIA_TYPE
    # One and sixty seconds of 16 kHz, 16-bit, mono audio.
    assert MAX_AUDIO_BYTES_PER_FRAME == 16_000 * 2
    assert MAX_AUDIO_BYTES_PER_CAPTURE == 60 * 16_000 * 2


def test_committed_examples_parse():
    envelope, payload = parse_client_message(_json(CONTRACTS / "examples" / "client" / "asr_start.json"))
    assert envelope.type == "asr.start" and isinstance(payload, AsrStartPayload)

    server = _json(CONTRACTS / "examples" / "server" / "asr_transcript.json")
    transcript = validate_server_payload(server["type"], server["payload"])
    assert transcript.is_final and transcript.capture_id == payload.capture_id
    assert server["request_id"] == str(envelope.request_id)

    header = MicrophoneFrameHeader.model_validate_json((CONTRACTS / "examples" / "client" / "microphone_frame_header.json").read_text())
    assert header.capture_id == payload.capture_id


def test_asr_start_rejects_unknown_fields_and_bad_ids():
    base = _json(CONTRACTS / "examples" / "client" / "asr_start.json")
    for payload in ({"capture_id": "not-a-uuid"}, {}, {"capture_id": str(uuid4()), "media_type": "audio/wav"}):
        with pytest.raises(ValidationError):
            parse_client_message({**base, "payload": payload})


def test_asr_transcript_allows_empty_final_but_bounds_text():
    ids = {"capture_id": str(uuid4()), "transcript_id": str(uuid4())}
    assert validate_server_payload("asr.transcript", {**ids, "text": "", "is_final": True}).text == ""
    with pytest.raises(ValidationError):
        validate_server_payload("asr.transcript", {**ids, "text": "x" * 8001, "is_final": True})
    with pytest.raises(ValidationError):
        validate_server_payload("asr.transcript", {**ids, "text": "hi", "is_final": True, "confidence": 0.9})


# ------------------------------------------------------------ framing


def test_frame_round_trips_header_and_audio():
    capture = uuid4()
    header, audio = _decoded(capture, sequence=3, audio=b"\x01\x00\x02\x00")
    assert header.capture_id == capture and header.sequence == 3 and audio == b"\x01\x00\x02\x00"


@pytest.mark.parametrize(
    "frame",
    [
        b"\x00\x00",
        struct.pack(">I", 0) + b"{}",
        struct.pack(">I", MAX_HEADER_BYTES + 1) + b"x" * (MAX_HEADER_BYTES + 1),
        struct.pack(">I", 50) + b"{}",
        struct.pack(">I", 3) + b"{x}",
    ],
    ids=["short", "zero-length", "oversized-header", "length-past-end", "bad-json"],
)
def test_unreadable_frames_are_rejected_before_or_at_parse(frame):
    with pytest.raises(UnreadableMicrophoneFrame):
        decode_microphone_frame(frame)


def test_declared_length_past_the_end_is_rejected_even_when_the_header_is_valid():
    header_bytes = json.dumps(_header(uuid4())).encode()
    with pytest.raises(UnreadableMicrophoneFrame):
        decode_microphone_frame(struct.pack(">I", len(header_bytes) + 1) + header_bytes)


@pytest.mark.parametrize(
    "override",
    [
        {"version": 2},
        {"version": True},
        {"media_type": "audio/mpeg"},
        {"sequence": "0"},
        {"sequence": -1},
        {"end_of_utterance": 1},
        {"capture_id": "capture-1"},
        {"generation_id": "gen-1"},
    ],
)
def test_headers_off_the_contract_are_unreadable(override):
    with pytest.raises(UnreadableMicrophoneFrame):
        decode_microphone_frame(_frame(_header(uuid4(), **override)))


def test_microphone_and_server_audio_framings_are_never_read_as_each_other():
    with pytest.raises(AudioFrameError):
        decode_audio_frame(_frame(_header(uuid4())))
    server_header = AudioFrameHeader(
        version=1, generation_id="g", segment_id="s", sequence=0, end_of_segment=True, end_of_generation=True, media_type="audio/mpeg"
    )
    with pytest.raises(UnreadableMicrophoneFrame):
        decode_microphone_frame(encode_audio_frame(server_header, b"\x00\x00"))


# ------------------------------------------------------------ capture rules


def _started(capture=None, request=None):
    gate = CaptureGate()
    capture, request = capture or uuid4(), request or uuid4()
    assert gate.start(capture, request) is None
    return gate, capture, request


def test_frames_in_order_are_forwarded_and_end_of_utterance_closes_the_capture():
    gate, capture, request = _started()
    assert gate.admit(*_decoded(capture, 0, audio=b"\x01\x00")) == b"\x01\x00"
    assert gate.admit(*_decoded(capture, 1, end=True)) == b""
    assert gate.open_request_id is None
    assert gate.admit(*_decoded(capture, 2, audio=b"\x01\x00")) is None


def test_frames_for_unknown_or_abandoned_captures_are_dropped_silently():
    gate, capture, request = _started()
    assert gate.admit(*_decoded(uuid4(), 0, audio=b"\x01\x00")) is None
    assert gate.abandon() == request
    assert gate.admit(*_decoded(capture, 0, audio=b"\x01\x00")) is None


@pytest.mark.parametrize("sequence", [1, 2], ids=["gap", "skip"])
def test_first_frame_must_be_sequence_zero(sequence):
    gate, capture, request = _started()
    with pytest.raises(CaptureViolation) as caught:
        gate.admit(*_decoded(capture, sequence))
    assert caught.value.request_id == request and caught.value.field == "sequence"


def test_duplicate_sequence_abandons_the_capture():
    gate, capture, request = _started()
    gate.admit(*_decoded(capture, 0, audio=b"\x01\x00"))
    with pytest.raises(CaptureViolation):
        gate.admit(*_decoded(capture, 0, audio=b"\x01\x00"))
    assert gate.open_request_id is None
    assert gate.admit(*_decoded(capture, 1, audio=b"\x01\x00")) is None


def test_odd_byte_count_is_a_violation():
    gate, capture, _ = _started()
    with pytest.raises(CaptureViolation) as caught:
        gate.admit(*_decoded(capture, 0, audio=b"\x01"))
    assert caught.value.field == "audio"


def test_one_second_per_frame_is_the_limit():
    gate, capture, _ = _started()
    assert gate.admit(*_decoded(capture, 0, audio=b"\x00" * MAX_AUDIO_BYTES_PER_FRAME)) is not None
    with pytest.raises(CaptureViolation):
        gate.admit(*_decoded(capture, 1, audio=b"\x00" * (MAX_AUDIO_BYTES_PER_FRAME + 2)))


def test_sixty_seconds_per_capture_is_the_limit():
    gate, capture, _ = _started()
    second = b"\x00" * MAX_AUDIO_BYTES_PER_FRAME
    for sequence in range(60):
        assert gate.admit(*_decoded(capture, sequence, audio=second)) is not None
    with pytest.raises(CaptureViolation):
        gate.admit(*_decoded(capture, 60, audio=b"\x00\x00"))


def test_new_capture_ends_the_open_one_and_ids_are_never_reused():
    gate, first, first_request = _started()
    second = uuid4()
    assert gate.start(second, uuid4()) == first_request
    assert gate.admit(*_decoded(first, 0, audio=b"\x01\x00")) is None
    assert gate.admit(*_decoded(second, 0, audio=b"\x01\x00")) == b"\x01\x00"
    with pytest.raises(InvalidRequestError) as caught:
        gate.start(first, uuid4())
    assert caught.value.field == "payload.capture_id"
