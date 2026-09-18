"""ElevenLabs adapter through the real elevenlabs==2.65.0 SDK with a controlled transport.

The SDK sends its real streaming request into ``httpx.MockTransport``; the
responder returns audio bytes in chunks. Then the adapter is driven by M1's
real SpeechOutput, so the frames it produces are the approved binary framing.
No network access and no claim about a real voice, model or audibility.
"""

import json

import httpx
import pytest

from netra_api.platform.errors import ProviderUnavailableError
from netra_api.speech.providers.elevenlabs import ElevenLabsSynthesizer, media_type_for
from netra_api.transport.audio.frame import decode_audio_frame

AUDIO = bytes(range(256)) * 64  # opaque stand-in bytes; not a claim of valid MP3 content


class StreamingResponder:
    def __init__(self, status=200):
        self.status, self.requests = status, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status, json={"detail": {"message": "echoed request text"}})
        return httpx.Response(200, content=AUDIO, headers={"content-type": "audio/mpeg"})


def _synth(responder, output_format="mp3_44100_128"):
    return ElevenLabsSynthesizer.from_api_key(
        "el-test-not-real", model_id="configured-model", voice_id="configured-voice", output_format=output_format,
        timeout_seconds=5, httpx_client=httpx.AsyncClient(transport=httpx.MockTransport(responder)))


async def test_the_sdk_streams_the_configured_voice_model_and_format():
    responder = StreamingResponder()
    chunks = [chunk async for chunk in _synth(responder).stream("It is written V = I × R.")]
    assert b"".join(chunks) == AUDIO
    request = responder.requests[0]
    assert request.url.path == "/v1/text-to-speech/configured-voice/stream"
    assert request.url.params["output_format"] == "mp3_44100_128"
    assert request.headers["xi-api-key"] == "el-test-not-real"
    body = json.loads(request.content)
    assert body["text"] == "It is written V = I × R." and body["model_id"] == "configured-model"


@pytest.mark.parametrize("status", [401, 429, 500])
async def test_provider_failures_are_unavailable_without_detail(status):
    responder = StreamingResponder(status)
    with pytest.raises(ProviderUnavailableError) as caught:
        async for _ in _synth(responder).stream("secret text"):
            pass
    assert "echoed" not in str(caught.value) and "secret" not in str(caught.value)
    assert len(responder.requests) == 1


def test_only_mp3_formats_are_accepted_for_the_client_player():
    assert media_type_for("mp3_22050_32") == "audio/mpeg"
    with pytest.raises(ValueError):
        media_type_for("pcm_16000")  # headerless PCM is not WAV


async def test_speech_output_frames_the_adapters_audio_with_the_approved_header():
    from datetime import datetime, timezone
    from uuid import uuid4

    from netra_api.platform.auth_context import AuthContext
    from netra_api.speech.playback_metadata import GenerationRegistry
    from netra_api.speech.quota import InMemoryQuotaLedger
    from netra_api.speech.synthesis import InMemoryAudioCache, SpeechOutput

    registry = GenerationRegistry()
    session_id = uuid4()
    generation = registry.start(session_id, uuid4())
    output = SpeechOutput(_synth(StreamingResponder()), InMemoryQuotaLedger(10_000), InMemoryAudioCache(), registry)
    frames = []

    async def send(frame):
        frames.append(frame)

    auth = AuthContext(account_id=uuid4(), session_id=session_id, request_id=uuid4(), issued_at=datetime.now(timezone.utc))
    assert await output.speak_segment(auth, generation, segment_id="seg-1", text="It is written V = I × R.",
                                      access_scope=f"account:{auth.account_id}", end_of_generation=True, send_bytes=send)
    decoded = [decode_audio_frame(frame) for frame in frames]
    assert b"".join(audio for _, audio in decoded) == AUDIO
    headers = [header for header, _ in decoded]
    assert all(h.media_type == "audio/mpeg" and h.generation_id == generation.generation_id for h in headers)
    assert [h.end_of_segment for h in headers][-1] and headers[-1].end_of_generation
    assert [h.sequence for h in headers] == sorted({h.sequence for h in headers})
