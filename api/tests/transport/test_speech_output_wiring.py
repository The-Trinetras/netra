"""A4: speech quota (D-QUOTA), the accessible limit notice and ElevenLabs composition.

LABELLED FIXTURE RUN: fixture synthesizer and in-memory ledger; the PostgreSQL
ledger runs in test_speech_quota_postgres.py (integration). No provider call.
"""

import asyncio

import pytest

from netra_api.bootstrap import production_dependencies
from netra_api.config import Settings
from netra_api.platform.database import create_engine
from netra_api.speech.quota import InMemoryQuotaLedger, SpeechQuotaExhaustedError
from netra_api.speech.synthesis import BoundedAudioCache, CachedAudio, InMemoryAudioCache, SpeechOutput
from netra_api.transport.websocket.endpoint import serve

from ohm_fixture import FakeSocket, build_journey, envelope, wait_for


async def _open(journey):
    socket = FakeSocket()
    task = asyncio.ensure_future(serve(socket, journey.services, journey.composition.verifier))
    await wait_for(lambda: socket.accepted)
    return socket, task


def _nav(command, version):
    return envelope("navigation.command", {"command": command, "expected_session_version": version})


async def test_exhausted_quota_keeps_text_and_tells_the_student_once_per_connection():
    journey = await build_journey(speech=True)
    journey.services.speech = SpeechOutput(journey.synthesizer, InMemoryQuotaLedger(characters_per_account=1), InMemoryAudioCache(), journey.services.generations)
    socket, task = await _open(journey)
    for version in (10, 11):
        socket.push(_nav("next", version))
        await wait_for(lambda: len(socket.of_type("response.segment")) >= version - 9)
    await asyncio.sleep(0.05)

    notices = [m for m in socket.texts if m["type"] == "error"]
    assert len(notices) == 1
    payload = notices[0]["payload"]
    assert payload["code"] == "RESOURCE_UNAVAILABLE" and payload["retryable"] is False
    assert payload["details"] == {"reason": "speech_quota_exhausted"}
    assert "speech limit" in payload["message"]
    assert socket.frames == []
    socket.disconnect()
    await task


async def test_other_speech_failures_stay_silent():
    class Broken:
        async def reserve(self, account_id, characters):
            from netra_api.platform.errors import ProviderUnavailableError

            raise ProviderUnavailableError("ledger down")

    journey = await build_journey(speech=True)
    journey.services.speech = SpeechOutput(journey.synthesizer, Broken(), InMemoryAudioCache(), journey.services.generations)
    socket, task = await _open(journey)
    socket.push(_nav("next", 10))
    await wait_for(lambda: socket.of_type("response.segment"))
    await asyncio.sleep(0.05)
    assert not [m for m in socket.texts if m["type"] == "error"] and socket.frames == []
    socket.disconnect()
    await task


async def test_in_memory_ledger_signals_exhaustion_specifically():
    from uuid import uuid4

    ledger = InMemoryQuotaLedger(characters_per_account=10)
    account = uuid4()
    await ledger.reserve(account, 10)
    with pytest.raises(SpeechQuotaExhaustedError):
        await ledger.reserve(account, 1)


async def test_bounded_cache_evicts_least_recently_used_and_skips_oversized():
    cache = BoundedAudioCache(max_bytes=10)
    await cache.put("a", CachedAudio(media_type="audio/mpeg", audio=b"x" * 4))
    await cache.put("b", CachedAudio(media_type="audio/mpeg", audio=b"x" * 4))
    assert await cache.get("a") is not None  # a is now most recent
    await cache.put("c", CachedAudio(media_type="audio/mpeg", audio=b"x" * 4))
    assert await cache.get("b") is None and await cache.get("a") is not None and await cache.get("c") is not None
    await cache.put("huge", CachedAudio(media_type="audio/mpeg", audio=b"x" * 11))
    assert await cache.get("huge") is None and await cache.get("c") is not None
    await cache.put("c", CachedAudio(media_type="audio/mpeg", audio=b"y" * 6))  # replaces c: 4 + 6 fits
    assert await cache.get("a") is not None
    await cache.put("c", CachedAudio(media_type="audio/mpeg", audio=b"y" * 7))  # 4 + 7 does not: a goes
    assert await cache.get("a") is None and (await cache.get("c")).audio == b"y" * 7


# ------------------------------------------------------------------ composition


def _wire(**settings):
    # The engine is never connected and the ElevenLabs client makes no call when built.
    engine = create_engine("postgresql+asyncpg://nobody:nothing@127.0.0.1:9/none")
    return production_dependencies(engine, None, Settings(**settings))


SPEECH = dict(elevenlabs_api_key="sk-test", elevenlabs_model_id="eleven_flash_v2_5", elevenlabs_voice_id="voice-1")


def test_elevenlabs_is_registered_with_the_daily_ledger_when_configured():
    from netra_api.speech.postgres import PostgresQuotaLedger

    speech = _wire(**SPEECH).speech_output
    assert speech is not None
    assert speech._synthesizer.config.media_type == "audio/mpeg"
    assert isinstance(speech._quota, PostgresQuotaLedger) and speech._quota._limit == 20_000
    assert isinstance(speech._cache, BoundedAudioCache)


@pytest.mark.parametrize("missing", ["elevenlabs_api_key", "elevenlabs_model_id", "elevenlabs_voice_id"])
def test_speech_stays_off_unless_key_model_and_voice_are_all_set(missing):
    assert _wire(**{k: v for k, v in SPEECH.items() if k != missing}).speech_output is None


def test_a_non_mp3_format_is_refused_at_startup():
    with pytest.raises(ValueError):
        _wire(**SPEECH, elevenlabs_output_format="pcm_16000")


def test_compose_binds_speech_to_the_transports_generation_registry():
    from netra_api.bootstrap import Repositories, UnavailableRepository, compose

    dependencies = _wire(**SPEECH)
    composition = compose(Settings(), Repositories(identity=UnavailableRepository(), sessions=UnavailableRepository()), dependencies, environ={})
    assert composition.services.speech is dependencies.speech_output
    assert composition.services.speech.registry is composition.services.generations
    assert composition.registered["speech"] is True


def test_env_names_map_to_the_settings(monkeypatch):
    for name, value in {"NETRA_ELEVENLABS_API_KEY": "sk-test", "NETRA_ELEVENLABS_MODEL_ID": "m",
                        "NETRA_ELEVENLABS_VOICE_ID": "v", "NETRA_ELEVENLABS_OUTPUT_FORMAT": "mp3_22050_32",
                        "NETRA_SPEECH_DAILY_CHARACTERS": "500"}.items():
        monkeypatch.setenv(name, value)
    settings = Settings()
    assert settings.elevenlabs_api_key.get_secret_value() == "sk-test"
    assert (settings.elevenlabs_model_id, settings.elevenlabs_voice_id, settings.elevenlabs_output_format) == ("m", "v", "mp3_22050_32")
    assert settings.speech_daily_characters == 500
