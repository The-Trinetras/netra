"""OpenRouter Tutor adapter (OpenAI-compatible chat completions over httpx).

Implements GroqTutorProvider for the Agent-a-thon's OpenRouter key, with the
same guarantees as groq_client: one attempt per call, reasoning configuration
left at the provider's defaults, and only the final message content returned
(any reasoning the model emits is never forwarded).

The model id is the adapter's own OpenRouter id; by default it is the approved
Tutor model, openai/gpt-oss-120b (see netra_api.config.Settings.openrouter_tutor_model).
"""

from __future__ import annotations

from typing import Optional

import httpx

from netra_api.learning.tutor.providers.groq import GroqTutorModelConfig, TutorModelDecision
from netra_api.platform.openrouter import build_client, chat_completion


class OpenRouterTutorAdapter:
    """GroqTutorProvider backed by OpenRouter."""

    def __init__(self, client: httpx.AsyncClient, api_key: str, model_id: str) -> None:
        self._client, self._api_key, self._model_id = client, api_key, model_id

    @classmethod
    def from_api_key(cls, api_key: str, *, model_id: str, timeout_seconds: float,
                     http_client: Optional[httpx.AsyncClient] = None) -> "OpenRouterTutorAdapter":
        if not api_key:
            raise ValueError("an OpenRouter API key is required")
        return cls(http_client or build_client(timeout_seconds), api_key, model_id)

    async def decide(self, config: GroqTutorModelConfig, prompt: str) -> TutorModelDecision:
        data = await chat_completion(self._client, self._api_key, {
            "model": self._model_id,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": config.temperature,
            "max_tokens": config.max_output_tokens,
        }, failure="tutor model request failed")
        choices = data.get("choices") or []
        if not choices:
            return TutorModelDecision(raw_text="", finish_reason="no_choice")
        choice = choices[0]
        content = (choice.get("message") or {}).get("content") or ""
        return TutorModelDecision(raw_text=content, finish_reason=str(choice.get("finish_reason") or "unknown"))


__all__ = ["OpenRouterTutorAdapter"]
