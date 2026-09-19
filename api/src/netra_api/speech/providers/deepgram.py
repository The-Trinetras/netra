"""Deepgram streaming recognition (deepgram-sdk==7.8.1, runtime baseline).

One provider WebSocket per push-to-talk capture (decision D-MIC): linear16,
16 kHz, mono, interim results on. Deepgram finalizes speech in segments;
this adapter joins the finalized segments, sends interim text as it
arrives, and after ``finish()`` (Finalize, then CloseStream) yields one
final event once the provider has flushed and closed. Provider errors are
ProviderUnavailableError with the exception class only, never provider text.
No model is assumed: the caller passes the configured one.
"""

from __future__ import annotations

from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any, AsyncIterator, Optional

from netra_api.platform.errors import ProviderUnavailableError
from netra_api.speech.recognition import TranscriptEvent

MAX_TRANSCRIPT_CHARS = 8000
"""turn.submit's utterance limit; a longer transcript is cut, never rejected."""


def _failure(exc: BaseException) -> ProviderUnavailableError:
    return ProviderUnavailableError(f"speech recognition failed ({type(exc).__name__})")


class _DeepgramSession:
    def __init__(self, socket: Any) -> None:
        self._socket = socket
        self._finished = False

    async def send_audio(self, audio: bytes) -> None:
        if not audio:
            return
        try:
            await self._socket.send_media(audio)
        except Exception as exc:
            raise _failure(exc) from None

    async def finish(self) -> None:
        self._finished = True
        try:
            await self._socket.send_finalize()
            await self._socket.send_close_stream()
        except Exception as exc:
            raise _failure(exc) from None

    async def transcripts(self) -> AsyncIterator[TranscriptEvent]:
        finals: list[str] = []
        try:
            async for message in self._socket:
                if getattr(message, "type", None) != "Results":
                    continue
                alternatives = message.channel.alternatives
                text = alternatives[0].transcript.strip() if alternatives else ""
                if message.is_final:
                    if text:
                        finals.append(text)
                    continue
                if text:
                    yield TranscriptEvent(text=" ".join([*finals, text])[:MAX_TRANSCRIPT_CHARS], is_final=False)
        except Exception as exc:
            raise _failure(exc) from None
        if not self._finished:
            raise ProviderUnavailableError("speech recognition ended before the capture finished")
        yield TranscriptEvent(text=" ".join(finals)[:MAX_TRANSCRIPT_CHARS], is_final=True)


class DeepgramRecognizer:
    """Recognizer (speech/recognition.py) over AsyncDeepgramClient.listen.v1."""

    def __init__(self, client: Any, *, model: str, language: Optional[str] = None) -> None:
        if not model:
            raise ValueError("a Deepgram model is required")
        self._client = client
        self._options = {
            "model": model,
            "encoding": "linear16",
            "sample_rate": "16000",
            "channels": "1",
            "interim_results": "true",
            "punctuate": "true",
            "smart_format": "true",
        }
        if language:
            self._options["language"] = language

    @classmethod
    def from_api_key(cls, api_key: str, *, model: str, language: Optional[str] = None,
                     timeout_seconds: float = 10.0) -> "DeepgramRecognizer":
        from deepgram import AsyncDeepgramClient

        if not api_key:
            raise ValueError("a Deepgram API key is required")
        return cls(AsyncDeepgramClient(api_key=api_key, timeout=timeout_seconds), model=model, language=language)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[_DeepgramSession]:
        async with AsyncExitStack() as stack:
            try:
                socket = await stack.enter_async_context(self._client.listen.v1.connect(**self._options))
            except Exception as exc:
                raise _failure(exc) from None
            yield _DeepgramSession(socket)


__all__ = ["DeepgramRecognizer"]
