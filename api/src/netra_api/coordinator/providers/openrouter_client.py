"""OpenRouter Coordinator adapter (OpenAI-compatible chat completions over httpx).

Implements GeminiCoordinatorProvider for the Agent-a-thon's OpenRouter key,
with the same guarantees as gemini_client: tools are offered as declarations
only (OpenRouter never executes them), every requested call comes back as an
inert ToolCallRequest that still passes through Netra's tool gateway, one
attempt per call, and reasoning the model returns is dropped, never forwarded.

``config.model_id`` names the native Gemini pin; this adapter sends its own
OpenRouter model id (by default the same approved model, see
netra_api.config.Settings.openrouter_coordinator_model). Temperature and
output limit still come from ``config``.
"""

from __future__ import annotations

import json
from typing import Any, Optional

import httpx

from netra_api.coordinator.providers.gemini import GeminiModelConfig, ModelDecision, ToolCallRequest, ToolSpec
from netra_api.platform.openrouter import build_client, chat_completion


class OpenRouterCoordinatorAdapter:
    provider_name = "openrouter"
    """GeminiCoordinatorProvider backed by OpenRouter."""

    def __init__(self, client: httpx.AsyncClient, api_key: str, model_id: str) -> None:
        self._client, self._api_key, self._model_id = client, api_key, model_id
        self.model_name = model_id

    @classmethod
    def from_api_key(cls, api_key: str, *, model_id: str, timeout_seconds: float,
                     http_client: Optional[httpx.AsyncClient] = None) -> "OpenRouterCoordinatorAdapter":
        if not api_key:
            raise ValueError("an OpenRouter API key is required")
        return cls(http_client or build_client(timeout_seconds), api_key, model_id)

    async def decide(self, config: GeminiModelConfig, prompt: str, tools: list[ToolSpec]) -> ModelDecision:
        body: dict[str, Any] = {
            "model": self._model_id,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": config.temperature,
            "max_tokens": config.max_output_tokens,
        }
        if tools:
            body["tools"] = [{"type": "function", "function": {
                "name": tool.name, "description": tool.description, "parameters": tool.input_schema}} for tool in tools]
        data = await chat_completion(self._client, self._api_key, body, failure="coordinator model request failed")
        return _decision(data)


def _decision(data: dict[str, Any]) -> ModelDecision:
    choices = data.get("choices") or []
    if not choices:
        return ModelDecision(raw_text="", finish_reason="no_candidate")
    choice = choices[0]
    message = choice.get("message") or {}  # "reasoning" is deliberately never read
    calls: list[ToolCallRequest] = []
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        try:
            arguments = json.loads(function.get("arguments") or "{}")
        except (TypeError, ValueError):
            arguments = None
        if not function.get("name") or not isinstance(arguments, dict):
            # A call we cannot read exactly is not guessed at: the whole decision
            # comes back empty, fails parse_decision, and the next decision is told.
            return ModelDecision(raw_text="", finish_reason="malformed_tool_call")
        calls.append(ToolCallRequest(tool_name=function["name"], arguments=arguments))
    return ModelDecision(raw_text=message.get("content") or "",
                         finish_reason=str(choice.get("finish_reason") or "unknown").lower(), tool_calls=calls)


__all__ = ["OpenRouterCoordinatorAdapter"]
