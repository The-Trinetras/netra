"""One OpenRouter chat-completions request, shared by the Coordinator and Tutor adapters.

OpenRouter is the provider behind the Agent-a-thon key. When configured it runs
both agents (decision D-AGENT); Gemini and Groq back an agent only without it.
See netra_api.bootstrap.production_dependencies.

Exactly one HTTP attempt per call and no client retries: every attempt is
counted against the originating turn's budget by the caller, never repeated
invisibly here. Failures become ProviderUnavailableError carrying only the
failure class or HTTP status, never the provider's message (which can echo
request content).
"""

from __future__ import annotations

from typing import Any

import httpx

from netra_api.platform.errors import ProviderUnavailableError

OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"

ROUTING = {"sort": "latency"}
"""Prefer the fastest host serving the pinned model. A turn has 20 seconds in
total, and OpenRouter's default routing favours price, which for
openai/gpt-oss-120b can pick a host several times slower than Groq's."""


def build_client(timeout_seconds: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout_seconds)


async def chat_completion(client: httpx.AsyncClient, api_key: str, body: dict[str, Any], *, failure: str) -> dict[str, Any]:
    """POST one request and return the decoded JSON body, or raise ProviderUnavailableError."""

    try:
        response = await client.post(OPENROUTER_CHAT_URL, json={**body, "provider": ROUTING},
                                     headers={"Authorization": f"Bearer {api_key}"})
    except httpx.HTTPError as exc:  # timeouts, connection and protocol errors
        raise ProviderUnavailableError(f"{failure} ({type(exc).__name__})") from None
    if response.status_code != 200:
        raise ProviderUnavailableError(f"{failure} (HTTP {response.status_code})") from None
    try:
        data = response.json()
    except ValueError:
        raise ProviderUnavailableError(f"{failure} (invalid JSON)") from None
    return data if isinstance(data, dict) else {}


__all__ = ["OPENROUTER_CHAT_URL", "build_client", "chat_completion"]
