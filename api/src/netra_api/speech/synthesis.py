"""Server speech synthesis control: cache, quota, streaming and cancellation fencing.

message-flow.md flow 6: validated public text goes to speech; speech checks
cache access and reserves quota before fresh synthesis; one active speaking
response; incomplete cancelled audio is never a completed cache entry;
sending audio does not advance played position. Flow 7: cancellation stops
further frames immediately, including frames already produced by the provider.

Frames use the approved binary framing (transport/audio/frame.py). Frame
sequence numbers are per generation and strictly increasing. The chunk size
below only bounds how cached audio is split; it is NOT an authoritative total
binary-message size limit, which remains an open M1/M5 decision.

Speech failure never blocks text: every failure path here returns without
audio after the text segment has already been delivered.
"""

from __future__ import annotations

import hashlib
import logging
from typing import AsyncIterator, Awaitable, Callable, Optional, Protocol

from pydantic import BaseModel, ConfigDict, Field

from netra_api.platform.auth_context import AuthContext
from netra_api.platform.errors import NetraError
from netra_api.speech.playback_metadata import Generation, GenerationRegistry
from netra_api.speech.quota import QuotaLedger
from netra_api.transport.audio.frame import AudioFrameHeader, encode_audio_frame

logger = logging.getLogger(__name__)

CACHED_AUDIO_CHUNK_BYTES = 32 * 1024
"""Split size for replaying cached audio. Implementation choice only; see module docstring."""

SendBytes = Callable[[bytes], Awaitable[None]]


class SynthesisConfig(BaseModel):
    """Explicit synthesis configuration; part of every cache identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    voice_id: str = Field(min_length=1)
    media_type: str = Field(min_length=1)


class SpeechSynthesizer(Protocol):
    config: SynthesisConfig

    def stream(self, text: str) -> AsyncIterator[bytes]:
        """Yield encoded audio chunks for public text. Provider objects never escape."""
        ...


class CachedAudio(BaseModel):
    model_config = ConfigDict(frozen=True)

    media_type: str
    audio: bytes


class AudioCache(Protocol):
    async def get(self, key: str) -> Optional[CachedAudio]:
        ...

    async def put(self, key: str, audio: CachedAudio) -> None:
        ...


class InMemoryAudioCache:
    """Non-durable completed-audio cache for tests and labelled fixtures."""

    def __init__(self) -> None:
        self.entries: dict[str, CachedAudio] = {}

    async def get(self, key: str) -> Optional[CachedAudio]:
        return self.entries.get(key)

    async def put(self, key: str, audio: CachedAudio) -> None:
        self.entries[key] = audio


def audio_cache_key(access_scope: str, config: SynthesisConfig, text: str) -> str:
    """Cache identity = access scope + synthesis configuration + exact text.

    access_scope is ``source:<source_version_id>`` for document reading (the
    caller re-authorizes the version before delivery) and
    ``account:<account_id>`` for generated text, so one account's generated
    explanation is never served to another account from cache.
    """

    material = "\x1f".join(
        [access_scope, config.provider, config.model_id, config.voice_id, config.media_type, text]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class SpeechOutput:
    def __init__(
        self,
        synthesizer: SpeechSynthesizer,
        quota: QuotaLedger,
        cache: AudioCache,
        registry: GenerationRegistry,
    ) -> None:
        self._synthesizer = synthesizer
        self._quota = quota
        self._cache = cache
        self._registry = registry

    async def speak_segment(
        self,
        auth: AuthContext,
        generation: Generation,
        *,
        segment_id: str,
        text: str,
        access_scope: str,
        end_of_generation: bool,
        send_bytes: SendBytes,
    ) -> bool:
        """Deliver one segment's audio. Returns True only if every frame was sent.

        Eligibility is checked before the cache lookup, before the quota
        reservation, and before every single frame, so STOP or disconnect
        stops audio even mid-stream.
        """

        if not generation.speakable or not await generation.wait_until_deliverable():
            return False

        config = self._synthesizer.config
        key = audio_cache_key(access_scope, config, text)
        cached = await self._cache.get(key)
        if cached is not None:
            chunks = [
                cached.audio[offset : offset + CACHED_AUDIO_CHUNK_BYTES]
                for offset in range(0, len(cached.audio), CACHED_AUDIO_CHUNK_BYTES)
            ] or [b""]
            return await self._send_chunks(generation, segment_id, cached.media_type, chunks, end_of_generation, send_bytes)

        try:
            await self._quota.reserve(auth.account_id, max(1, len(text)))
        except NetraError as exc:
            logger.info("speech skipped for segment: %s", type(exc).__name__)
            return False

        collected: list[bytes] = []
        pending: Optional[bytes] = None
        try:
            async for chunk in self._synthesizer.stream(text):
                if not chunk:
                    continue
                if pending is not None:
                    if not await self._send_frame(generation, segment_id, config.media_type, pending, False, False, send_bytes):
                        return False
                    collected.append(pending)
                pending = chunk
        except NetraError as exc:
            logger.info("speech provider failed for segment: %s", type(exc).__name__)
            return False
        except Exception as exc:  # provider boundary: never leak provider detail
            logger.warning("speech provider error for segment: %s", type(exc).__name__)
            return False

        final_chunk = pending if pending is not None else b""
        if not await self._send_frame(
            generation, segment_id, config.media_type, final_chunk, True, end_of_generation, send_bytes
        ):
            return False
        collected.append(final_chunk)
        await self._cache.put(key, CachedAudio(media_type=config.media_type, audio=b"".join(collected)))
        return True

    async def _send_chunks(self, generation, segment_id, media_type, chunks, end_of_generation, send_bytes) -> bool:
        for index, chunk in enumerate(chunks):
            last = index == len(chunks) - 1
            if not await self._send_frame(
                generation, segment_id, media_type, chunk, last, last and end_of_generation, send_bytes
            ):
                return False
        return True

    async def _send_frame(
        self,
        generation: Generation,
        segment_id: str,
        media_type: str,
        audio: bytes,
        end_of_segment: bool,
        end_of_generation: bool,
        send_bytes: SendBytes,
    ) -> bool:
        if not await generation.wait_until_deliverable():
            return False
        header = AudioFrameHeader(
            version=1,
            generation_id=generation.generation_id,
            segment_id=segment_id,
            sequence=self._registry.next_frame_sequence(generation),
            end_of_segment=end_of_segment,
            end_of_generation=end_of_generation,
            media_type=media_type,
        )
        await send_bytes(encode_audio_frame(header, audio))
        return True
