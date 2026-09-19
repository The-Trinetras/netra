"""Gemini and Groq adapters through the real pinned SDKs with a controlled local transport.

The SDKs build and send real HTTP requests into ``httpx.MockTransport`` and
parse its responses, so request shapes, header authentication, tool
declarations, response parsing, retry behaviour and error mapping are
verified against google-genai==2.21.0 and groq==1.7.0 without network access.
This is not evidence that the configured model ids exist on either provider.
"""

import json

import httpx
import pytest

from netra_api.coordinator.decisions import parse_decision
from netra_api.coordinator.providers.gemini import DEFAULT_COORDINATOR_MODEL_CONFIG, ToolSpec
from netra_api.coordinator.providers.gemini_client import GeminiCoordinatorAdapter
from netra_api.learning.tutor.providers.groq import GroqTutorModelConfig
from netra_api.learning.tutor.providers.groq_client import GroqTutorAdapter
from netra_api.platform.errors import ProviderUnavailableError

SEARCH = ToolSpec(name="search_sources", description="Search the pinned source.",
                  input_schema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]})


class Recorder:
    def __init__(self, *responses):
        self.responses, self.requests = list(responses), []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        status, body = self.responses.pop(0)
        return httpx.Response(status, json=body)


def _gemini(recorder):
    return GeminiCoordinatorAdapter.from_api_key(
        "test-key-not-real", timeout_seconds=5, httpx_async_client=httpx.AsyncClient(transport=httpx.MockTransport(recorder)))


async def test_gemini_sends_declarations_only_and_returns_inert_tool_calls():
    recorder = Recorder((200, {"candidates": [{"content": {"role": "model", "parts": [
        {"text": "private reasoning", "thought": True},
        {"functionCall": {"name": "search_sources", "args": {"query": "table rows"}}}]}, "finishReason": "STOP"}]}))
    decision = await _gemini(recorder).decide(DEFAULT_COORDINATOR_MODEL_CONFIG, "Why is R constant?", [SEARCH])

    request = recorder.requests[0]
    assert request.method == "POST" and request.url.path.endswith("/models/gemini-3.8-flash:generateContent")
    assert request.headers["x-goog-api-key"] == "test-key-not-real"
    body = json.loads(request.content)
    assert body["contents"][0]["parts"][0]["text"] == "Why is R constant?"
    declaration = body["tools"][0]["functionDeclarations"][0]
    assert declaration["name"] == "search_sources" and declaration["parameters_json_schema"] == SEARCH.input_schema  # SDK 2.21.0 wire form
    assert body["generationConfig"]["temperature"] == DEFAULT_COORDINATOR_MODEL_CONFIG.temperature
    assert body["generationConfig"]["maxOutputTokens"] == DEFAULT_COORDINATOR_MODEL_CONFIG.max_output_tokens

    assert decision.raw_text == "" and decision.finish_reason == "stop"
    assert [(c.tool_name, c.arguments) for c in decision.tool_calls] == [("search_sources", {"query": "table rows"})]
    assert parse_decision(decision).payload.action == "call_tools"


async def test_gemini_final_json_decision_parses_and_reasoning_is_dropped():
    final = {"action": "answer", "text": "Voltage rises 2 V per ampere.", "cited_evidence_ids": ["ev-1"]}
    recorder = Recorder((200, {"candidates": [{"content": {"parts": [
        {"text": "thinking about it", "thought": True}, {"text": json.dumps(final)}]}, "finishReason": "STOP"}]}))
    decision = await _gemini(recorder).decide(DEFAULT_COORDINATOR_MODEL_CONFIG, "q", [])
    assert json.loads(decision.raw_text) == final and "tools" not in json.loads(recorder.requests[0].content)


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_gemini_failures_are_provider_unavailable_after_exactly_one_attempt(status):
    recorder = Recorder((status, {"error": {"code": status, "message": "echoed prompt text", "status": "X"}}))
    with pytest.raises(ProviderUnavailableError) as caught:
        await _gemini(recorder).decide(DEFAULT_COORDINATOR_MODEL_CONFIG, "secret prompt", [SEARCH])
    assert len(recorder.requests) == 1  # no hidden retry outside the turn budget
    assert "echoed" not in str(caught.value) and "secret" not in str(caught.value)


def _groq(recorder):
    return GroqTutorAdapter.from_api_key(
        "gsk-test-not-real", timeout_seconds=5, http_client=httpx.AsyncClient(transport=httpx.MockTransport(recorder)))


def _completion(content, reasoning=None, finish="stop"):
    message = {"role": "assistant", "content": content}
    if reasoning is not None:
        message["reasoning"] = reasoning
    return {"id": "chatcmpl-1", "object": "chat.completion", "created": 0, "model": "openai/gpt-oss-120b",
            "choices": [{"index": 0, "message": message, "finish_reason": finish}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}


async def test_groq_sends_the_pinned_model_and_returns_only_final_content():
    recorder = Recorder((200, _completion("Each row is 2 volts per ampere.", reasoning="private chain")))
    decision = await _groq(recorder).decide(GroqTutorModelConfig(), "Explain the table.")

    request = recorder.requests[0]
    assert request.url.path == "/openai/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer gsk-test-not-real"
    body = json.loads(request.content)
    assert body["model"] == "openai/gpt-oss-120b" and body["stream"] is False
    assert body["messages"] == [{"role": "user", "content": "Explain the table."}]
    assert body["max_completion_tokens"] == GroqTutorModelConfig().max_output_tokens
    assert "reasoning_effort" not in body and "include_reasoning" not in body  # provider defaults unchanged
    assert decision.raw_text == "Each row is 2 volts per ampere." and decision.finish_reason == "stop"


@pytest.mark.parametrize("status", [401, 429, 500])
async def test_groq_failures_are_provider_unavailable_after_exactly_one_attempt(status):
    recorder = Recorder((status, {"error": {"message": "echoed prompt text", "type": "x"}}))
    with pytest.raises(ProviderUnavailableError) as caught:
        await _groq(recorder).decide(GroqTutorModelConfig(), "secret prompt")
    assert len(recorder.requests) == 1
    assert "echoed" not in str(caught.value)
