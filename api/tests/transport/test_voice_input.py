"""B1 push-to-talk over the real transport (D-MIC): asr.start, microphone frames, asr.transcript.

LABELLED FIXTURE RUN: a scripted recognizer stands in for Deepgram; the
Deepgram adapter itself is tested in test_deepgram_adapter.py with a fake SDK
socket. No provider call. The server only transcribes: no turn is created.
"""

import asyncio
import json
import struct
from contextlib import asynccontextmanager
from uuid import uuid4

from netra_api.platform.errors import ProviderUnavailableError
from netra_api.speech.recognition import TranscriptEvent
from netra_api.transport.websocket.endpoint import serve

from ohm_fixture import SESSION, FakeSocket, ScriptedModel, build_journey, envelope, wait_for

_END = object()


class FakeSession:
    def __init__(self, final_text, hold_final=None, fail_after=None):
        self.audio = bytearray()
        self.finished = False
        self.finishes = 0
        self.closed = False
        self.final_text = final_text
        self.hold_final = hold_final
        self.fail_after = fail_after
        self._events: asyncio.Queue = asyncio.Queue()

    async def send_audio(self, audio):
        self.audio += audio
        await self._events.put(TranscriptEvent(text="Explain", is_final=False))
        if self.fail_after is not None and len(self.audio) >= self.fail_after:
            await self._events.put(ProviderUnavailableError("provider dropped"))

    async def finish(self):
        self.finished = True
        self.finishes += 1
        await self._events.put(_END)

    async def transcripts(self):
        while True:
            item = await self._events.get()
            if isinstance(item, Exception):
                raise item
            if item is _END:
                if self.hold_final is not None:
                    await self.hold_final.wait()
                yield TranscriptEvent(text=self.final_text, is_final=True)
                return
            yield item


class FakeRecognizer:
    def __init__(self, final_text="Explain the table.", fail_open=False, hold_final=None, fail_after=None):
        self.sessions: list[FakeSession] = []
        self.final_text, self.fail_open, self.hold_final, self.fail_after = final_text, fail_open, hold_final, fail_after

    @asynccontextmanager
    async def session(self):
        if self.fail_open:
            raise ProviderUnavailableError("speech recognition failed (ApiError)")
        session = FakeSession(self.final_text, self.hold_final, self.fail_after)
        self.sessions.append(session)
        try:
            yield session
        finally:
            session.closed = True


def frame(capture, sequence, audio=b"\x01\x00\x02\x00", end=False, **overrides):
    header = {"version": 1, "capture_id": str(capture), "sequence": sequence, "end_of_utterance": end, "media_type": "audio/L16;rate=16000"}
    header.update(overrides)
    raw = json.dumps(header).encode()
    return struct.pack(">I", len(raw)) + raw + audio


async def _setup(recognizer=None, model=None):
    journey = await build_journey(model=model)
    journey.services.recognizer = recognizer if recognizer is not None else FakeRecognizer()
    socket = FakeSocket()
    task = asyncio.ensure_future(serve(socket, journey.services, journey.composition.verifier))
    await wait_for(lambda: socket.accepted)
    return journey, socket, task


async def _start(socket, capture=None):
    capture = capture or uuid4()
    message = envelope("asr.start", {"capture_id": str(capture)})
    socket.push(message)
    return capture, message["request_id"]


def _send(socket, data):
    socket.inbox.put_nowait({"type": "websocket.receive", "bytes": data})


def _of(socket, request_id, message_type):
    return [m for m in socket.texts if m["request_id"] == request_id and m["type"] == message_type]


async def _close(socket, task):
    socket.disconnect()
    await asyncio.wait_for(task, 2)


async def test_capture_relays_interims_then_exactly_one_final_and_creates_no_turn():
    model = ScriptedModel([])
    journey, socket, task = await _setup(model=model)
    capture, request = await _start(socket)
    await wait_for(lambda: journey.services.recognizer.sessions)
    for sequence in range(3):
        _send(socket, frame(capture, sequence, end=sequence == 2))
    await wait_for(lambda: any(m["payload"]["is_final"] for m in _of(socket, request, "asr.transcript")))
    await asyncio.sleep(0.02)

    transcripts = [m["payload"] for m in _of(socket, request, "asr.transcript")]
    finals = [t for t in transcripts if t["is_final"]]
    assert len(finals) == 1 and finals[0]["text"] == "Explain the table."
    assert all(not t["is_final"] for t in transcripts[:-1]) and transcripts[-1]["is_final"]
    assert {t["capture_id"] for t in transcripts} == {str(capture)} and len({t["transcript_id"] for t in transcripts}) == 1
    session = journey.services.recognizer.sessions[0]
    assert bytes(session.audio) == b"\x01\x00\x02\x00" * 3 and session.finished and session.closed
    assert model.calls == 0 and (await journey.sessions.get(SESSION)).session_version == 10
    await _close(socket, task)


async def test_nothing_recognised_is_an_empty_final():
    journey, socket, task = await _setup(FakeRecognizer(final_text="   "))
    capture, request = await _start(socket)
    await wait_for(lambda: journey.services.recognizer.sessions)
    _send(socket, frame(capture, 0, audio=b"", end=True))
    await wait_for(lambda: _of(socket, request, "asr.transcript"))
    assert [m["payload"]["text"] for m in _of(socket, request, "asr.transcript")] == [""]
    await _close(socket, task)


async def test_a_frame_fault_abandons_the_capture_with_an_error_and_no_final():
    journey, socket, task = await _setup()
    capture, request = await _start(socket)
    await wait_for(lambda: journey.services.recognizer.sessions)
    _send(socket, frame(capture, 0))
    _send(socket, frame(capture, 2))  # gap
    await wait_for(lambda: _of(socket, request, "error"))
    error = _of(socket, request, "error")[0]["payload"]
    assert error["code"] == "INVALID_REQUEST" and error["details"] == {"field": "sequence"}
    session = journey.services.recognizer.sessions[0]
    await wait_for(lambda: session.closed)
    _send(socket, frame(capture, 3, end=True))
    await asyncio.sleep(0.05)
    assert not [m for m in _of(socket, request, "asr.transcript") if m["payload"]["is_final"]]
    assert bytes(session.audio) == b"\x01\x00\x02\x00" and not session.finished
    await _close(socket, task)


async def test_a_new_press_ends_the_open_capture_without_a_final():
    journey, socket, task = await _setup()
    first, first_request = await _start(socket)
    await wait_for(lambda: len(journey.services.recognizer.sessions) == 1)
    _send(socket, frame(first, 0))
    second, second_request = await _start(socket)
    await wait_for(lambda: len(journey.services.recognizer.sessions) == 2)
    assert journey.services.recognizer.sessions[0].closed
    _send(socket, frame(first, 1, end=True))  # late frame for the ended capture: dropped
    _send(socket, frame(second, 0, end=True))
    await wait_for(lambda: any(m["payload"]["is_final"] for m in _of(socket, second_request, "asr.transcript")))
    assert not [m for m in _of(socket, first_request, "asr.transcript") if m["payload"]["is_final"]]
    await _close(socket, task)


async def test_a_released_capture_still_delivers_its_final_after_a_new_press():
    hold = asyncio.Event()
    journey, socket, task = await _setup(FakeRecognizer(hold_final=hold))
    first, first_request = await _start(socket)
    await wait_for(lambda: len(journey.services.recognizer.sessions) == 1)
    _send(socket, frame(first, 0, end=True))
    await wait_for(lambda: journey.services.recognizer.sessions[0].finished)
    await _start(socket)
    await wait_for(lambda: len(journey.services.recognizer.sessions) == 2)
    hold.set()
    await wait_for(lambda: any(m["payload"]["is_final"] for m in _of(socket, first_request, "asr.transcript")))
    await _close(socket, task)


async def test_stop_ends_listening_with_no_final():
    journey, socket, task = await _setup()
    capture, request = await _start(socket)
    await wait_for(lambda: journey.services.recognizer.sessions)
    _send(socket, frame(capture, 0))
    socket.push(envelope("navigation.command", {"command": "stop", "expected_session_version": 10}))
    session = journey.services.recognizer.sessions[0]
    await wait_for(lambda: session.closed)
    _send(socket, frame(capture, 1, end=True))
    await asyncio.sleep(0.05)
    assert not session.finished
    assert not [m for m in _of(socket, request, "asr.transcript") if m["payload"]["is_final"]]
    await _close(socket, task)


async def test_disconnect_closes_the_provider_session():
    journey, socket, task = await _setup()
    capture, _ = await _start(socket)
    await wait_for(lambda: journey.services.recognizer.sessions)
    _send(socket, frame(capture, 0))
    await _close(socket, task)
    assert journey.services.recognizer.sessions[0].closed


async def test_provider_unavailable_at_start_is_typed_and_frames_are_dropped():
    journey, socket, task = await _setup(FakeRecognizer(fail_open=True))
    capture, request = await _start(socket)
    await wait_for(lambda: _of(socket, request, "error"))
    assert _of(socket, request, "error")[0]["payload"]["code"] == "PROVIDER_UNAVAILABLE"
    _send(socket, frame(capture, 0, end=True))
    await asyncio.sleep(0.05)
    assert socket.closed_code is None and not _of(socket, request, "asr.transcript")
    await _close(socket, task)


async def test_provider_failure_mid_capture_is_typed_and_ends_the_capture():
    journey, socket, task = await _setup(FakeRecognizer(fail_after=4))
    capture, request = await _start(socket)
    await wait_for(lambda: journey.services.recognizer.sessions)
    _send(socket, frame(capture, 0))
    await wait_for(lambda: _of(socket, request, "error"))
    assert _of(socket, request, "error")[0]["payload"]["code"] == "PROVIDER_UNAVAILABLE"
    await wait_for(lambda: journey.services.recognizer.sessions[0].closed)
    await _close(socket, task)


async def test_a_capture_id_is_never_reused():
    journey, socket, task = await _setup()
    capture, _ = await _start(socket)
    await wait_for(lambda: journey.services.recognizer.sessions)
    _, again = await _start(socket, capture)
    await wait_for(lambda: _of(socket, again, "error"))
    assert _of(socket, again, "error")[0]["payload"]["code"] == "INVALID_REQUEST"
    assert len(journey.services.recognizer.sessions) == 1
    await _close(socket, task)


async def test_server_audio_framing_from_the_client_closes_the_connection():
    from netra_api.transport.audio.frame import AudioFrameHeader, encode_audio_frame
    from netra_api.transport.websocket.endpoint import CLOSE_INVALID_PAYLOAD

    journey, socket, task = await _setup()
    header = AudioFrameHeader(version=1, generation_id="g", segment_id="s", sequence=0, end_of_segment=True, end_of_generation=True, media_type="audio/mpeg")
    _send(socket, encode_audio_frame(header, b"\x00\x00"))
    await asyncio.wait_for(task, 2)
    assert socket.closed_code == CLOSE_INVALID_PAYLOAD


async def test_late_frames_after_release_are_dropped_and_finished_captures_are_forgotten(monkeypatch):
    import netra_api.transport.websocket.endpoint as endpoint

    connections = []

    class Recording(endpoint.Connection):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            connections.append(self)

    monkeypatch.setattr(endpoint, "Connection", Recording)
    hold = asyncio.Event()
    journey, socket, task = await _setup(FakeRecognizer(hold_final=hold))
    capture, request = await _start(socket)
    await wait_for(lambda: journey.services.recognizer.sessions)
    _send(socket, frame(capture, 0, end=True))
    session = journey.services.recognizer.sessions[0]
    await wait_for(lambda: session.finished)
    _send(socket, frame(capture, 1, end=True))  # a late duplicate release while the final is pending
    await asyncio.sleep(0.05)
    hold.set()
    await wait_for(lambda: any(m["payload"]["is_final"] for m in _of(socket, request, "asr.transcript")))
    await asyncio.sleep(0.02)
    assert session.finishes == 1 and bytes(session.audio) == b"\x01\x00\x02\x00"
    assert connections[0]._recognitions == {}
    await _close(socket, task)
