"""ElevenLabs synthesis adapter (elevenlabs==2.65.0, AsyncElevenLabs streaming).

Implements ``SpeechSynthesizer`` (netra_api.speech.synthesis): public text in,
raw encoded audio chunks out; SDK objects never escape. Model, voice and output
format are explicit configuration with no defaults (no approved values exist
in the repository), and they are part of every audio cache identity.

Only MP3 output formats are accepted: the WPF client plays ``audio/mpeg`` and
``audio/wav``, and ElevenLabs ``pcm_*`` output is headerless PCM, not WAV, so
mapping it to a WAV media type would be a lie the player discovers later.

Provider failures raise ProviderUnavailableError with the failure class only;
SpeechOutput then stops audio for the segment while text remains delivered.
The SDK's own HTTP retries are not configured; each synthesis is one attempt.
"""

from __future__ import annotations

from typing import Any, AsyncIterator

from netra_api.platform.errors import ProviderUnavailableError
from netra_api.speech.synthesis import SpeechSynthesizer, SynthesisConfig

MP3_MEDIA_TYPE = "audio/mpeg"


def media_type_for(output_format: str) -> str:
    if output_format.startswith("mp3_"):
        return MP3_MEDIA_TYPE
    raise ValueError("only mp3_* ElevenLabs output formats are supported by the client player")


class ElevenLabsSynthesizer:
    """SpeechSynthesizer over ``AsyncElevenLabs.text_to_speech.stream``."""

    def __init__(self, client: Any, *, model_id: str, voice_id: str, output_format: str) -> None:
        if not (model_id and voice_id and output_format):
            raise ValueError("model_id, voice_id and output_format are required")
        self._client = client
        self._output_format = output_format
        self.config = SynthesisConfig(provider="elevenlabs", model_id=model_id, voice_id=voice_id,
                                      media_type=media_type_for(output_format))

    @classmethod
    def from_api_key(cls, api_key: str, *, model_id: str, voice_id: str, output_format: str,
                     timeout_seconds: float, httpx_client: Any = None) -> "ElevenLabsSynthesizer":
        from elevenlabs.client import AsyncElevenLabs

        if not api_key:
            raise ValueError("an ElevenLabs API key is required")
        client = AsyncElevenLabs(api_key=api_key, timeout=timeout_seconds, httpx_client=httpx_client)
        return cls(client, model_id=model_id, voice_id=voice_id, output_format=output_format)

    async def stream(self, text: str) -> AsyncIterator[bytes]:
        from elevenlabs.core.api_error import ApiError

        try:
            async for chunk in self._client.text_to_speech.stream(
                    self.config.voice_id, text=text, model_id=self.config.model_id,
                    output_format=self._output_format):
                if chunk:
                    yield bytes(chunk)
        except ApiError as exc:
            raise ProviderUnavailableError(f"speech synthesis failed ({type(exc).__name__})") from None
        except ProviderUnavailableError:
            raise
        except Exception as exc:  # transport failures; never leak provider detail
            raise ProviderUnavailableError(f"speech synthesis failed ({type(exc).__name__})") from None


def build_elevenlabs_synthesizer(*, api_key: str, model_id: str, voice_id: str, output_format: str,
                                 timeout_seconds: float) -> SpeechSynthesizer:
    return ElevenLabsSynthesizer.from_api_key(api_key, model_id=model_id, voice_id=voice_id,
                                              output_format=output_format, timeout_seconds=timeout_seconds)


__all__ = ["ElevenLabsSynthesizer", "build_elevenlabs_synthesizer", "media_type_for"]
