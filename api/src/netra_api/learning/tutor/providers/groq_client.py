"""Groq Tutor adapter over groq==1.7.0 (AsyncGroq chat completions).

Implements GroqTutorProvider for the approved model ``openai/gpt-oss-120b``.
SDK retries are disabled (``max_retries=0``): every model attempt must be
counted against the originating turn's shared budget by the Tutor loop, not
repeated invisibly inside the SDK. Reasoning configuration is left at the
provider's defaults (no silent change), and only the final message content is
returned: any reasoning the model emits is never forwarded.

Provider failures become ProviderUnavailableError carrying only the failure
class, never the provider message (which can echo request content).
"""

from __future__ import annotations

from typing import Any

from netra_api.learning.tutor.providers.groq import GroqTutorModelConfig, TutorModelDecision
from netra_api.platform.errors import ProviderUnavailableError


class GroqTutorAdapter:
    """GroqTutorProvider backed by ``groq.AsyncGroq``."""

    def __init__(self, client: Any) -> None:
        self._client = client

    @classmethod
    def from_api_key(cls, api_key: str, *, timeout_seconds: float, http_client: Any = None) -> "GroqTutorAdapter":
        from groq import AsyncGroq

        if not api_key:
            raise ValueError("a Groq API key is required")
        return cls(AsyncGroq(api_key=api_key, timeout=timeout_seconds, max_retries=0, http_client=http_client))

    async def decide(self, config: GroqTutorModelConfig, prompt: str) -> TutorModelDecision:
        import groq

        try:
            completion = await self._client.chat.completions.create(
                model=config.model_id,
                messages=[{"role": "user", "content": prompt}],
                temperature=config.temperature,
                max_completion_tokens=config.max_output_tokens,
                stream=False,
            )
        except groq.GroqError as exc:
            raise ProviderUnavailableError(f"tutor model request failed ({type(exc).__name__})") from None
        choices = getattr(completion, "choices", None) or []
        if not choices:
            return TutorModelDecision(raw_text="", finish_reason="no_choice")
        choice = choices[0]
        content = getattr(choice.message, "content", None) or ""
        return TutorModelDecision(raw_text=content, finish_reason=str(choice.finish_reason or "unknown"))


__all__ = ["GroqTutorAdapter"]
