"""Deepgram adapter (deepgram-sdk 7.8.1) against a fake SDK socket; no network."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from netra_api.bootstrap import production_dependencies
from netra_api.config import Settings
from netra_api.platform.database import create_engine
from netra_api.platform.errors import ProviderUnavailableError
from netra_api.speech.providers.deepgram import MAX_TRANSCRIPT_CHARS, DeepgramRecognizer


def results(text, is_final):
    return SimpleNamespace(type="Results", is_final=is_final, channel=SimpleNamespace(alternatives=[SimpleNamespace(transcript=text)]))


class FakeSocket:
    def __init__(self, messages, fail_iter=False):
        self.messages, self.fail_iter = messages, fail_iter
        self.media, self.finalized, self.closed_stream = [], False, False

    async def send_media(self, data):
        self.media.append(data)

    async def send_finalize(self):
        self.finalized = True

    async def send_close_stream(self):
        self.closed_stream = True

    async def __aiter__(self):
        for message in self.messages:
            yield message
        if self.fail_iter:
            raise ConnectionError("socket dropped")


def fake_client(socket=None, fail_connect=False):
    calls = []

    @asynccontextmanager
    async def connect(**options):
        calls.append(options)
        if fail_connect:
            raise ConnectionRefusedError("provider says: bad key sk-secret")
        yield socket

    return SimpleNamespace(listen=SimpleNamespace(v1=SimpleNamespace(connect=connect))), calls


async def _collect(recognizer, feed=(), finish=True):
    async with recognizer.session() as session:
        for audio in feed:
            await session.send_audio(audio)
        if finish:
            await session.finish()
        return [event async for event in session.transcripts()]


async def test_stream_options_are_linear16_16k_mono_with_interim_results():
    client, calls = fake_client(FakeSocket([]))
    await _collect(DeepgramRecognizer(client, model="nova-x", language="en-IN"))
    assert calls == [{"model": "nova-x", "encoding": "linear16", "sample_rate": "16000", "channels": "1",
                      "interim_results": "true", "punctuate": "true", "smart_format": "true", "language": "en-IN"}]
    client, calls = fake_client(FakeSocket([]))
    await _collect(DeepgramRecognizer(client, model="nova-x"))
    assert "language" not in calls[0]


async def test_finalized_segments_are_joined_into_one_final_after_finish():
    socket = FakeSocket([
        results("explain", False),
        results("Explain the", True),
        SimpleNamespace(type="Metadata"),
        results("tab", False),
        results("", True),
        results("table.", True),
    ])
    client, _ = fake_client(socket)
    events = await _collect(DeepgramRecognizer(client, model="m"), feed=[b"\x01\x00", b""])
    assert [(e.text, e.is_final) for e in events] == [
        ("explain", False), ("Explain the tab", False), ("Explain the table.", True),
    ]
    assert socket.media == [b"\x01\x00"] and socket.finalized and socket.closed_stream


async def test_provider_closing_before_finish_is_a_failure_not_a_final():
    client, _ = fake_client(FakeSocket([results("half", True)]))
    with pytest.raises(ProviderUnavailableError):
        await _collect(DeepgramRecognizer(client, model="m"), finish=False)


async def test_failures_carry_only_the_exception_class():
    client, _ = fake_client(fail_connect=True)
    with pytest.raises(ProviderUnavailableError) as caught:
        async with DeepgramRecognizer(client, model="m").session():
            pass
    assert str(caught.value) == "speech recognition failed (ConnectionRefusedError)"

    client, _ = fake_client(FakeSocket([results("a", True)], fail_iter=True))
    with pytest.raises(ProviderUnavailableError) as caught:
        await _collect(DeepgramRecognizer(client, model="m"))
    assert "sk-secret" not in str(caught.value) and "ConnectionError" in str(caught.value)


async def test_a_very_long_transcript_is_cut_to_the_turn_limit():
    client, _ = fake_client(FakeSocket([results("word " * 2000, True)]))
    events = await _collect(DeepgramRecognizer(client, model="m"))
    assert len(events[-1].text) == MAX_TRANSCRIPT_CHARS


def test_a_model_is_required():
    with pytest.raises(ValueError):
        DeepgramRecognizer(object(), model="")


def _wire(**settings):
    engine = create_engine("postgresql+asyncpg://nobody:nothing@127.0.0.1:9/none")
    return production_dependencies(engine, None, Settings(**settings))


def test_voice_input_is_registered_only_with_both_key_and_model():
    assert isinstance(_wire(deepgram_api_key="dg-test", deepgram_model="nova-x").recognizer, DeepgramRecognizer)
    assert _wire(deepgram_api_key="dg-test").recognizer is None
    assert _wire(deepgram_model="nova-x").recognizer is None
