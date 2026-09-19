"""OpenRouter adapters for the Coordinator and Tutor, against a controlled local transport.

httpx sends real requests into ``httpx.MockTransport``, so request shape,
authentication, tool declarations, response parsing and error mapping are
checked without network access. This is not evidence that either model
answers well, or at all, on OpenRouter today.
"""

import json

import httpx
import pytest

from netra_api.bootstrap import production_dependencies
from netra_api.config import Settings
from netra_api.coordinator.decisions import InvalidDecisionError, parse_decision
from netra_api.coordinator.providers.gemini import DEFAULT_COORDINATOR_MODEL_CONFIG, ToolSpec
from netra_api.coordinator.providers.gemini_client import GeminiCoordinatorAdapter
from netra_api.coordinator.providers.openrouter_client import OpenRouterCoordinatorAdapter
from netra_api.learning.tutor.providers.groq import GroqTutorModelConfig
from netra_api.learning.tutor.providers.groq_client import GroqTutorAdapter
from netra_api.learning.tutor.providers.openrouter_client import OpenRouterTutorAdapter
from netra_api.platform.database import create_engine
from netra_api.platform.errors import ProviderUnavailableError

KEY = "sk-or-test-not-real"
SEARCH = ToolSpec(name="search_sources", description="Search the pinned source.",
                  input_schema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]})


class Recorder:
    def __init__(self, *responses):
        self.responses, self.requests = list(responses), []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        status, body = self.responses.pop(0)
        return httpx.Response(status, json=body)


def _client(recorder):
    return httpx.AsyncClient(transport=httpx.MockTransport(recorder))


def _coordinator(recorder, model="google/gemini-3.8-flash"):
    return OpenRouterCoordinatorAdapter.from_api_key(KEY, model_id=model, timeout_seconds=5, http_client=_client(recorder))


def _tutor(recorder, model="openai/gpt-oss-120b"):
    return OpenRouterTutorAdapter.from_api_key(KEY, model_id=model, timeout_seconds=5, http_client=_client(recorder))


def _completion(message, finish="stop"):
    return {"id": "gen-1", "object": "chat.completion", "model": "m",
            "choices": [{"index": 0, "message": {"role": "assistant", **message}, "finish_reason": finish}]}


async def test_coordinator_sends_declarations_only_and_returns_inert_tool_calls():
    recorder = Recorder((200, _completion({"content": None, "reasoning": "private chain", "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "search_sources", "arguments": "{\"query\": \"table rows\"}"}}]},
        finish="tool_calls")))
    decision = await _coordinator(recorder).decide(DEFAULT_COORDINATOR_MODEL_CONFIG, "Why is R constant?", [SEARCH])

    request = recorder.requests[0]
    assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    body = json.loads(request.content)
    assert body["model"] == "google/gemini-3.8-flash"  # the adapter's pin, not the native Gemini id
    assert body["messages"] == [{"role": "user", "content": "Why is R constant?"}]
    assert body["tools"] == [{"type": "function", "function": {
        "name": "search_sources", "description": SEARCH.description, "parameters": SEARCH.input_schema}}]
    assert body["temperature"] == DEFAULT_COORDINATOR_MODEL_CONFIG.temperature
    assert body["max_tokens"] == DEFAULT_COORDINATOR_MODEL_CONFIG.max_output_tokens
    assert body["provider"] == {"sort": "latency"}

    assert decision.raw_text == "" and decision.finish_reason == "tool_calls"
    assert [(c.tool_name, c.arguments) for c in decision.tool_calls] == [("search_sources", {"query": "table rows"})]
    assert "private chain" not in decision.model_dump_json()
    assert parse_decision(decision).payload.action == "call_tools"


async def test_coordinator_final_json_decision_parses_and_no_tools_are_offered_when_none_exist():
    final = {"action": "answer", "text": "Voltage rises 2 V per ampere.", "cited_evidence_ids": ["ev-1"]}
    recorder = Recorder((200, _completion({"content": json.dumps(final), "reasoning": "thinking"})))
    decision = await _coordinator(recorder).decide(DEFAULT_COORDINATOR_MODEL_CONFIG, "q", [])
    assert json.loads(decision.raw_text) == final and decision.finish_reason == "stop"
    assert "tools" not in json.loads(recorder.requests[0].content)


@pytest.mark.parametrize("arguments", ["{\"query\": ", "[\"not\", \"an\", \"object\"]", None])
async def test_coordinator_never_guesses_at_a_tool_call_it_cannot_read(arguments):
    recorder = Recorder((200, _completion({"content": None, "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "search_sources", "arguments": arguments}}]})))
    decision = await _coordinator(recorder).decide(DEFAULT_COORDINATOR_MODEL_CONFIG, "q", [SEARCH])
    if arguments is None:  # absent arguments are an empty object, which the tool's schema then judges
        assert decision.tool_calls[0].arguments == {}
        return
    assert decision.tool_calls == [] and decision.finish_reason == "malformed_tool_call"
    with pytest.raises(InvalidDecisionError):
        parse_decision(decision)


async def test_coordinator_with_no_choices_is_an_empty_decision():
    decision = await _coordinator(Recorder((200, {"choices": []}))).decide(DEFAULT_COORDINATOR_MODEL_CONFIG, "q", [])
    assert decision.raw_text == "" and decision.finish_reason == "no_candidate"


@pytest.mark.parametrize("status", [401, 402, 429, 500, 503])
async def test_coordinator_failures_are_provider_unavailable_after_exactly_one_attempt(status):
    recorder = Recorder((status, {"error": {"code": status, "message": "echoed prompt text"}}))
    with pytest.raises(ProviderUnavailableError) as caught:
        await _coordinator(recorder).decide(DEFAULT_COORDINATOR_MODEL_CONFIG, "secret prompt", [SEARCH])
    assert len(recorder.requests) == 1  # no hidden retry outside the turn budget
    assert f"HTTP {status}" in str(caught.value)
    assert "echoed" not in str(caught.value) and "secret" not in str(caught.value) and KEY not in str(caught.value)


async def test_transport_failure_carries_only_its_class():
    def refuse(request):
        raise httpx.ConnectError("connection to host with secret prompt refused")

    adapter = OpenRouterCoordinatorAdapter.from_api_key(KEY, model_id="m/x", timeout_seconds=5,
                                                        http_client=_client(refuse))
    with pytest.raises(ProviderUnavailableError) as caught:
        await adapter.decide(DEFAULT_COORDINATOR_MODEL_CONFIG, "secret prompt", [])
    assert str(caught.value) == "coordinator model request failed (ConnectError)"


async def test_tutor_sends_its_model_and_returns_only_final_content():
    recorder = Recorder((200, _completion({"content": "Each row is 2 volts per ampere.", "reasoning": "private chain"})))
    decision = await _tutor(recorder).decide(GroqTutorModelConfig(), "Explain the table.")

    body = json.loads(recorder.requests[0].content)
    assert recorder.requests[0].headers["authorization"] == f"Bearer {KEY}"
    assert body["model"] == "openai/gpt-oss-120b" and "tools" not in body
    assert body["messages"] == [{"role": "user", "content": "Explain the table."}]
    assert body["max_tokens"] == GroqTutorModelConfig().max_output_tokens
    assert "reasoning" not in body and "reasoning_effort" not in body  # provider defaults unchanged
    assert decision.raw_text == "Each row is 2 volts per ampere." and decision.finish_reason == "stop"


@pytest.mark.parametrize("status", [401, 402, 429, 500])
async def test_tutor_failures_are_provider_unavailable_after_exactly_one_attempt(status):
    recorder = Recorder((status, {"error": {"message": "echoed prompt text"}}))
    with pytest.raises(ProviderUnavailableError) as caught:
        await _tutor(recorder).decide(GroqTutorModelConfig(), "secret prompt")
    assert len(recorder.requests) == 1 and "echoed" not in str(caught.value)


def test_an_empty_key_is_refused():
    with pytest.raises(ValueError):
        OpenRouterTutorAdapter.from_api_key("", model_id="m/x", timeout_seconds=5)


def _wiring(**keys):
    # The engine is never connected: composing the dependencies opens no session.
    engine = create_engine("postgresql+asyncpg://nobody:nothing@127.0.0.1:9/none")
    return production_dependencies(engine, None, Settings(**keys))


def test_openrouter_fills_both_agent_slots_when_no_native_key_is_set():
    wired = _wiring(openrouter_api_key=KEY, gemini_api_key=None, groq_api_key=None)
    assert isinstance(wired.coordinator_model, OpenRouterCoordinatorAdapter)
    assert isinstance(wired.tutor_services.provider, OpenRouterTutorAdapter)
    assert wired.coordinator_model._model_id == "google/gemini-3.8-flash"
    assert wired.tutor_services.provider._model_id == "openai/gpt-oss-120b"


def test_a_native_key_is_never_silently_replaced_by_openrouter():
    wired = _wiring(openrouter_api_key=KEY, gemini_api_key="g-test", groq_api_key="gsk-test")
    assert isinstance(wired.coordinator_model, GeminiCoordinatorAdapter)
    assert isinstance(wired.tutor_services.provider, GroqTutorAdapter)


def test_no_key_registers_no_agent():
    wired = _wiring(openrouter_api_key=None, gemini_api_key=None, groq_api_key=None)
    assert wired.coordinator_model is None and wired.tutor_services is None


def test_a_bare_kit_key_does_not_configure_the_api(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    monkeypatch.delenv("NETRA_OPENROUTER_API_KEY", raising=False)
    assert Settings().openrouter_api_key is None
    monkeypatch.setenv("NETRA_OPENROUTER_API_KEY", KEY)
    monkeypatch.setenv("NETRA_OPENROUTER_TUTOR_MODEL", "inclusionai/ling-3.0-flash")
    settings = Settings()
    assert settings.openrouter_api_key.get_secret_value() == KEY
    assert settings.openrouter_tutor_model == "inclusionai/ling-3.0-flash"
