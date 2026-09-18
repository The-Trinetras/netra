"""Coordinator → Tutor on the real server with the real adapters and real storage.

Real: uvicorn, HTTP/WebSocket over TCP, production composition
(`production_dependencies` + `compose`), PostgreSQL retrieval (full-text
reduced mode), the async evidence resolver, M1's engine and gateway, M4's
Tutor loop, the google-genai and groq SDKs and Netra's adapters.
Controlled: only the two providers' HTTP transports (``httpx.MockTransport``
answering like the provider APIs). No network call is made; this is not
live-provider evidence.
"""

import json
import os
from dataclasses import replace
from uuid import UUID, uuid4

import httpx
import pytest
from websockets.asyncio.client import connect

from netra_api.bootstrap import compose, durable_repositories, production_dependencies
from netra_api.config import Settings
from netra_api.coordinator.limits import MAX_MODEL_DECISIONS_PER_TURN
from netra_api.coordinator.providers.gemini_client import GeminiCoordinatorAdapter
from netra_api.learning.tutor.providers.groq_client import GroqTutorAdapter
from netra_api.main import create_app

from server_support import ProtocolClient, running_app, seed_account_and_source

pytestmark = pytest.mark.integration


class ScriptedProviderHttp:
    """Answers like the provider APIs, in order; records every request."""

    def __init__(self, bodies):
        self.bodies, self.requests = list(bodies), []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        if not self.bodies:
            return httpx.Response(500, json={"error": {"message": "unexpected extra call"}})
        return httpx.Response(200, json=self.bodies.pop(0))


def _gemini_parts(*parts):
    return {"candidates": [{"content": {"role": "model", "parts": list(parts)}, "finishReason": "STOP"}]}


def _groq(content):
    return {"id": "c", "object": "chat.completion", "created": 0, "model": "openai/gpt-oss-120b",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}]}


async def test_a_turn_delegates_to_the_tutor_through_real_adapters_and_storage():
    url = os.environ.get("NETRA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("NETRA_TEST_DATABASE_URL (a disposable local database) is not set")
    seeded = await seed_account_and_source(url)
    evidence_id = str(seeded.chunk_id)
    gemini_http = ScriptedProviderHttp([
        _gemini_parts({"text": json.dumps({"action": "call_tools", "requirements": [
                          {"requirement_id": "rows", "description": "the table rows"}]})},
                      {"functionCall": {"name": "search_sources", "args": {"query": "table rows"}}}),
        _gemini_parts({"text": json.dumps({
            "action": "delegate_to_tutor",
            "assessments": [{"requirement_id": "rows", "status": "supported", "evidence_id": evidence_id}],
            "tutor": {"mode": "explain", "learning_goal": "Explain why the resistance is constant.",
                      "target_concept_ids": ["ohms-law"], "evidence_ids": [evidence_id]}})}),
    ])
    groq_http = ScriptedProviderHttp([_groq("Each row gives 2 volts per ampere, so the resistance is 2 ohms.")])

    settings = Settings(database_url=url, auth_mode="stored_credential", trace_to_log=False,
                        gemini_api_key="gem-test-not-real", groq_api_key="gsk-test-not-real")
    repositories = durable_repositories(settings)
    dependencies = production_dependencies(repositories.engine, None, settings)
    dependencies.coordinator_model = GeminiCoordinatorAdapter.from_api_key(
        "gem-test-not-real", timeout_seconds=5,
        httpx_async_client=httpx.AsyncClient(transport=httpx.MockTransport(gemini_http)))
    dependencies.tutor_services = replace(dependencies.tutor_services, provider=GroqTutorAdapter.from_api_key(
        "gsk-test-not-real", timeout_seconds=5, http_client=httpx.AsyncClient(transport=httpx.MockTransport(groq_http))))
    composition = compose(settings, repositories, dependencies)
    assert composition.registered["coordinator_model"] and composition.registered["tutor"]

    async with running_app(settings, app=create_app(composition=composition)) as host, \
            httpx.AsyncClient(base_url=f"http://{host}") as http:
        auth = {"Authorization": f"Bearer {seeded.token}"}
        session_id = UUID((await http.post("/v1/sessions", headers=auth)).json()["session_id"])
        pinned = await http.post(f"/v1/sessions/{session_id}/source", headers=auth, json={
            "request_id": str(uuid4()), "source_version_id": str(seeded.source_version_id), "expected_session_version": 0})
        assert pinned.status_code == 200

        async with connect(f"ws://{host}/v1/ws", additional_headers=auth, proxy=None) as ws:
            client = ProtocolClient(ws, session_id)
            turn_id = uuid4()
            submit = {"utterance": "Why is the resistance constant in table 1?", "input_mode": "keyboard",
                      "transcript_status": "final", "expected_session_version": 1}
            frames = await client.settle(await client.send("turn.submit", submit, request_id=turn_id), quiet=0.6)
            assert all(m["type"] != "error" for m in frames), frames
            segments = [m["payload"] for m in frames if m["type"] == "response.segment"]
            assert [s["text"] for s in segments] == ["Each row gives 2 volts per ampere, so the resistance is 2 ohms."]
            assert segments[0]["evidence_ids"] == [evidence_id]
            snapshot = [m["payload"] for m in frames if m["type"] == "session.snapshot"][-1]
            assert snapshot["interaction_mode"] == "tutor_lesson" and snapshot["active_lesson"] is not None

            # The Tutor was given the real resolver's identity for the evidence (INT-03).
            tutor_prompt = groq_http.requests[0]["messages"][0]["content"]
            assert "(1 A, 2 V)" in tutor_prompt and len(gemini_http.requests) == 2
            assert len(gemini_http.requests) + len(groq_http.requests) <= MAX_MODEL_DECISIONS_PER_TURN
            # The Coordinator offered only permitted tools, as declarations.
            offered = {d["name"] for d in gemini_http.requests[0]["tools"][0]["functionDeclarations"]}
            assert "search_sources" in offered

            # A retransmission of the same turn replays the committed result: no new provider call.
            replay = await client.settle(await client.send("turn.submit", submit, request_id=turn_id), quiet=0.6)
            assert [m["payload"]["text"] for m in replay if m["type"] == "response.segment"] == [s["text"] for s in segments]
            assert len(gemini_http.requests) == 2 and len(groq_http.requests) == 1
    await repositories.engine.dispose()
