"""STOP, pause/continue, disconnect and stale audio through the real delivery path.

LABELLED FIXTURE: the synthesizer is a fake provider and quota/cache are
in-memory. This proves server-side fencing; it does not prove client-side
local STOP latency (M5/WPF) or live ElevenLabs behaviour.
"""

import asyncio
from uuid import uuid4

from netra_api.speech.playback_metadata import GenerationRegistry
from netra_api.speech.quota import InMemoryQuotaLedger
from netra_api.speech.synthesis import InMemoryAudioCache, SpeechOutput, audio_cache_key
from netra_api.transport.audio.frame import decode_audio_frame
from netra_api.transport.websocket.endpoint import serve

from ohm_fixture import SESSION, FakeSocket, FixtureSynthesizer, build_journey, envelope, sid, wait_for


async def _open(journey):
    socket = FakeSocket()
    task = asyncio.ensure_future(serve(socket, journey.services, journey.composition.verifier))
    await wait_for(lambda: socket.accepted)
    return socket, task


def _nav(command, version, unit=None):
    payload = {"command": command, "expected_session_version": version}
    if unit:
        payload["navigation_unit"] = unit
    return envelope("navigation.command", payload)


async def test_frames_follow_approved_framing_with_increasing_sequence():
    journey = await build_journey(speech=True)
    socket, task = await _open(journey)
    socket.push(_nav("next", 10, unit="block"))
    await wait_for(lambda: any(decode_audio_frame(f)[0].end_of_generation for f in socket.frames))
    headers = [decode_audio_frame(frame)[0] for frame in socket.frames]
    assert [h.sequence for h in headers] == sorted({h.sequence for h in headers})
    assert headers[-1].end_of_segment and headers[-1].end_of_generation
    assert len({h.generation_id for h in headers}) == 1
    socket.disconnect()
    await task


async def test_stop_during_synthesis_blocks_every_later_frame_and_ack():
    gate = asyncio.Event()
    journey = await build_journey(speech=True, synthesizer=FixtureSynthesizer(chunks=4, gate=gate))
    socket, task = await _open(journey)
    socket.push(_nav("next", 10))
    await wait_for(lambda: len(socket.frames) >= 1)
    segment = socket.of_type("response.segment")[0]["payload"]

    socket.push(_nav("stop", 11))
    await wait_for(lambda: len(socket.of_type("session.snapshot")) >= 2)
    frames_at_stop = len(socket.frames)
    gate.set()
    await asyncio.sleep(0.1)
    assert len(socket.frames) == frames_at_stop
    generation = journey.services.generations.get(SESSION, segment["generation_id"])
    assert generation.state == "cancelled" and generation.cancel_reason == "user_stop"

    socket.push(envelope("playback.ack", {"generation_id": segment["generation_id"], "segment_id": segment["segment_id"], "sentence_id": segment["sentence_id"], "status": "completed"}))
    await wait_for(lambda: socket.of_type("error"))
    assert socket.of_type("error")[0]["payload"]["code"] == "STALE_REQUEST"
    assert (await journey.sessions.get(SESSION)).session_version == 11  # STOP itself moved nothing
    assert not journey.cache.entries  # incomplete cancelled audio is never cached
    socket.disconnect()
    await task


async def test_continue_after_stop_starts_new_output_and_never_resurrects_cancelled():
    gate = asyncio.Event()
    journey = await build_journey(speech=True, synthesizer=FixtureSynthesizer(chunks=3, gate=gate))
    socket, task = await _open(journey)
    socket.push(_nav("next", 10))
    await wait_for(lambda: socket.frames)
    old_generation = socket.of_type("response.segment")[0]["payload"]["generation_id"]
    socket.push(_nav("stop", 11))
    await wait_for(lambda: len(socket.of_type("session.snapshot")) >= 2)

    socket.push(_nav("continue", 11))
    await wait_for(lambda: len(socket.of_type("response.segment")) >= 2)
    gate.set()
    new_generation = socket.of_type("response.segment")[-1]["payload"]["generation_id"]
    assert new_generation != old_generation
    await wait_for(lambda: any(decode_audio_frame(f)[0].generation_id == new_generation and decode_audio_frame(f)[0].end_of_generation for f in socket.frames))
    assert journey.services.generations.get(SESSION, old_generation).state == "cancelled"
    assert all(decode_audio_frame(f)[0].generation_id == new_generation for f in socket.frames[-2:])
    socket.disconnect()
    await task


async def test_pause_then_continue_resumes_the_same_eligible_generation():
    gate = asyncio.Event()
    journey = await build_journey(speech=True, synthesizer=FixtureSynthesizer(chunks=3, gate=gate))
    socket, task = await _open(journey)
    socket.push(_nav("next", 10))
    await wait_for(lambda: socket.frames)
    generation_id = socket.of_type("response.segment")[0]["payload"]["generation_id"]

    socket.push(_nav("pause", 11))
    await wait_for(lambda: len(socket.of_type("session.snapshot")) >= 2)
    gate.set()
    await asyncio.sleep(0.05)
    paused_frames = len(socket.frames)
    assert journey.services.generations.get(SESSION, generation_id).state == "paused"

    socket.push(_nav("continue", 11))
    await wait_for(lambda: len(socket.frames) > paused_frames)
    assert decode_audio_frame(socket.frames[-1])[0].generation_id == generation_id
    socket.disconnect()
    await task


async def test_disconnect_fences_late_audio_and_reconnect_does_not_revive_it():
    gate = asyncio.Event()
    journey = await build_journey(speech=True, synthesizer=FixtureSynthesizer(chunks=4, gate=gate))
    socket, task = await _open(journey)
    socket.push(_nav("next", 10))
    await wait_for(lambda: socket.frames)
    generation_id = socket.of_type("response.segment")[0]["payload"]["generation_id"]
    socket.disconnect()
    await task
    frames = len(socket.frames)
    gate.set()
    await asyncio.sleep(0.05)
    assert len(socket.frames) == frames
    assert journey.services.generations.get(SESSION, generation_id).cancel_reason == "disconnect"

    second, second_task = await _open(journey)
    second.push(envelope("session.resume", {"last_known_session_version": 11}))
    await wait_for(lambda: second.of_type("session.snapshot"))
    await asyncio.sleep(0.05)
    assert second.frames == []
    snapshot = second.of_type("session.snapshot")[0]["payload"]
    assert snapshot["last_acknowledged_sentence_id"] == str(sid("b12", "s2"))  # sent is not played
    second.disconnect()
    await second_task


async def test_quota_exhaustion_keeps_text_and_skips_audio():
    journey = await build_journey(speech=True)
    journey.services.speech = SpeechOutput(journey.synthesizer, InMemoryQuotaLedger(characters_per_account=1), InMemoryAudioCache(), journey.services.generations)
    socket, task = await _open(journey)
    socket.push(_nav("next", 10))
    await wait_for(lambda: socket.of_type("response.segment"))
    await asyncio.sleep(0.05)
    assert socket.frames == []
    socket.disconnect()
    await task


async def test_cache_identity_includes_access_scope_and_configuration():
    config = FixtureSynthesizer.config
    text = "Voltage equals current times resistance."
    assert audio_cache_key("account:a", config, text) != audio_cache_key("account:b", config, text)
    assert audio_cache_key("source:x", config, text) != audio_cache_key("source:x", config.model_copy(update={"voice_id": "other"}), text)


async def test_completed_audio_is_cached_and_replayed_without_new_synthesis_or_quota():
    registry = GenerationRegistry()
    synthesizer = FixtureSynthesizer(chunks=2)
    quota = InMemoryQuotaLedger(characters_per_account=10_000)
    output = SpeechOutput(synthesizer, quota, InMemoryAudioCache(), registry)
    sent: list[bytes] = []

    async def send(frame):
        sent.append(frame)

    from ohm_fixture import ACCOUNT
    from netra_api.platform.auth_context import AuthContext
    from datetime import datetime, timezone

    auth = AuthContext(account_id=ACCOUNT, session_id=SESSION, request_id=uuid4(), issued_at=datetime.now(timezone.utc))
    for _ in range(2):
        generation = registry.start(SESSION, uuid4())
        assert await output.speak_segment(auth, generation, segment_id="seg-0", text="hello there", access_scope="source:v", end_of_generation=True, send_bytes=send)
    assert synthesizer.streams == 1
    assert len(quota.reservations) == 1
