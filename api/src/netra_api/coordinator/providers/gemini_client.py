"""Gemini Coordinator adapter over google-genai==2.21.0 (async client).

Implements GeminiCoordinatorProvider. Tools are offered as function
declarations only: SDK automatic function calling is disabled, so every
requested call comes back as an inert ToolCallRequest and still passes
through Netra's tool gateway (authorization, budget, audit). The SDK's own
retries stay off (``retry_options=None`` means one attempt), because each
attempt must be counted against the originating turn's 4-decision budget by
the Coordinator, not repeated invisibly inside the SDK.

Provider failures become ProviderUnavailableError carrying only the failure
class, never the provider's message or payload. Thought/reasoning parts, if a
model returns any, are dropped: private reasoning is never forwarded.
"""

from __future__ import annotations

from typing import Any, Optional

from netra_api.coordinator.providers.gemini import GeminiModelConfig, ModelDecision, ToolCallRequest, ToolSpec
from netra_api.platform.errors import ProviderUnavailableError


class GeminiCoordinatorAdapter:
    provider_name = "google"
    """GeminiCoordinatorProvider backed by ``genai.Client(...).aio``."""

    def __init__(self, client: Any) -> None:
        self._client = client

    @classmethod
    def from_api_key(cls, api_key: str, *, timeout_seconds: float, httpx_async_client: Any = None) -> "GeminiCoordinatorAdapter":
        from google import genai
        from google.genai import types

        if not api_key:
            raise ValueError("a Gemini API key is required")
        options = types.HttpOptions(timeout=int(timeout_seconds * 1000), retry_options=None,
                                    httpx_async_client=httpx_async_client)
        return cls(genai.Client(api_key=api_key, http_options=options))

    async def decide(self, config: GeminiModelConfig, prompt: str, tools: list[ToolSpec]) -> ModelDecision:
        from google.genai import errors, types

        declared = [types.FunctionDeclaration(name=tool.name, description=tool.description,
                                              parameters_json_schema=tool.input_schema) for tool in tools]
        generation = types.GenerateContentConfig(
            temperature=config.temperature,
            max_output_tokens=config.max_output_tokens,
            tools=[types.Tool(function_declarations=declared)] if declared else None,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        try:
            response = await self._client.aio.models.generate_content(
                model=config.model_id, contents=prompt, config=generation)
        except errors.APIError as exc:
            raise ProviderUnavailableError(f"coordinator model request failed ({type(exc).__name__})") from None
        except Exception as exc:  # transport failures (timeouts, connection errors)
            raise ProviderUnavailableError(f"coordinator model request failed ({type(exc).__name__})") from None
        return _decision(response)


def _decision(response: Any) -> ModelDecision:
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return ModelDecision(raw_text="", finish_reason="no_candidate")
    candidate = candidates[0]
    text_parts: list[str] = []
    calls: list[ToolCallRequest] = []
    content = getattr(candidate, "content", None)
    for part in (getattr(content, "parts", None) or []):
        if getattr(part, "thought", None):
            continue  # private reasoning is never forwarded
        call = getattr(part, "function_call", None)
        if call is not None and call.name:
            calls.append(ToolCallRequest(tool_name=call.name, arguments=dict(call.args or {})))
        elif getattr(part, "text", None):
            text_parts.append(part.text)
    return ModelDecision(raw_text="".join(text_parts), finish_reason=_finish(candidate), tool_calls=calls)


def _finish(candidate: Any) -> str:
    reason: Optional[Any] = getattr(candidate, "finish_reason", None)
    if reason is None:
        return "unknown"
    return str(getattr(reason, "value", reason)).lower()


__all__ = ["GeminiCoordinatorAdapter"]
