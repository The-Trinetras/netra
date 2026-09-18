"""Real FastAPI/uvicorn process + real HTTP/WebSocket + disposable PostgreSQL.

Covers what needs no model provider: authentication at the HTTP and upgrade
boundaries, session creation, source listing and explicit pinning with replay
and version semantics, deterministic navigation over real M2 reading blocks,
replay of a navigation command, reconnect/resume, turns failing explicitly
without a Coordinator adapter, and reconciliation of a question the Learning
store already closed.
"""

import os
from datetime import datetime, timezone
from uuid import UUID, uuid4

import httpx
import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

from netra_api.config import Settings
from netra_api.learning.assessment.models import AnswerSubmission, AssessmentAttempt, AttemptOutcome
from netra_api.learning.postgres import PostgresLearningStore
from netra_api.learning.quiz.models import AnswerKey, ApprovedQuestion, QuestionKind, QuestionOption
from netra_api.platform.auth_context import AuthContext
from netra_api.platform.database import create_engine, create_session_factory
from netra_api.session.modes import InteractionMode
from netra_api.session.postgres import PostgresSessionRepository
from netra_api.session.service import SessionService
from netra_api.session.state import PendingQuestionRef

from server_support import ProtocolClient, running_app, seed_account_and_source

pytestmark = pytest.mark.integration


@pytest.fixture
def database_url():
    url = os.environ.get("NETRA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("NETRA_TEST_DATABASE_URL (a disposable local database) is not set")
    return url


def _settings(url):
    return Settings(database_url=url, auth_mode="stored_credential", trace_to_log=False)


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


async def test_http_and_websocket_journey_over_real_m2_content(database_url):
    seeded = await seed_account_and_source(database_url)
    other = await seed_account_and_source(database_url, title="another student's notes")
    async with running_app(_settings(database_url)) as host, httpx.AsyncClient(base_url=f"http://{host}") as http:
        live = (await http.get("/health/live")).json()
        assert live["registered"]["persistence"] and live["registered"]["authentication"] and live["registered"]["reading"]

        # Authentication is required at every route; an unknown token is refused.
        assert (await http.post("/v1/sessions")).status_code == 401
        refused = await http.post("/v1/sessions", headers=_bearer("not-a-real-token"))
        assert refused.status_code == 401 and refused.json()["error"]["code"] == "AUTH_REQUIRED"

        created = await http.post("/v1/sessions", headers=_bearer(seeded.token))
        assert created.status_code == 201
        session_id = UUID(created.json()["session_id"])
        snapshot = created.json()["snapshot"]
        assert snapshot["session_version"] == 0 and snapshot["interaction_mode"] == "idle"

        listed = (await http.get(f"/v1/sessions/{session_id}/sources", headers=_bearer(seeded.token))).json()
        assert [s["active_source_version_id"] for s in listed["sources"]] == [str(seeded.source_version_id)]
        # The other account cannot use this session id.
        assert (await http.get(f"/v1/sessions/{session_id}/sources", headers=_bearer(other.token))).status_code == 403

        select = {"request_id": str(uuid4()), "source_version_id": str(seeded.source_version_id),
                  "expected_session_version": 0}
        pinned = await http.post(f"/v1/sessions/{session_id}/source", headers=_bearer(seeded.token), json=select)
        assert pinned.status_code == 200 and not pinned.json()["replayed"]
        assert pinned.json()["snapshot"]["active_source_version_id"] == str(seeded.source_version_id)
        assert pinned.json()["snapshot"]["current_block_id"] == str(seeded.block_ids[0])
        # Identical retransmission replays; reuse with a different payload fails closed.
        replay = await http.post(f"/v1/sessions/{session_id}/source", headers=_bearer(seeded.token),
                                 json={**select, "source_version_id": str(seeded.source_version_id).upper()})
        assert replay.status_code == 200 and replay.json()["replayed"] and replay.json()["snapshot"] == pinned.json()["snapshot"]
        conflict = await http.post(f"/v1/sessions/{session_id}/source", headers=_bearer(seeded.token),
                                   json={**select, "expected_session_version": 5})
        assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "REQUEST_ID_CONFLICT"
        stale = await http.post(f"/v1/sessions/{session_id}/source", headers=_bearer(seeded.token),
                                json={**select, "request_id": str(uuid4())})
        assert stale.status_code == 409 and stale.json()["error"]["code"] == "SESSION_VERSION_CONFLICT"
        assert stale.json()["error"]["current_session_version"] == 1
        # Another account's source version cannot be pinned (existence is not revealed).
        foreign = await http.post(f"/v1/sessions/{session_id}/source", headers=_bearer(seeded.token), json={
            "request_id": str(uuid4()), "source_version_id": str(other.source_version_id), "expected_session_version": 1})
        assert foreign.status_code == 403 and foreign.json()["error"]["code"] == "AUTHORIZATION_DENIED"

        # WebSocket: the upgrade is refused without a credential.
        with pytest.raises(InvalidStatus):
            async with connect(f"ws://{host}/v1/ws", proxy=None):
                pass

        async with connect(f"ws://{host}/v1/ws", additional_headers=_bearer(seeded.token), proxy=None) as ws:
            client = ProtocolClient(ws, session_id)
            resumed = await client.until(await client.send("session.resume", {"last_known_session_version": 1}),
                                         "session.snapshot")
            assert resumed[-1]["payload"]["session_version"] == 1

            nav_id = uuid4()
            first = await client.settle(await client.send(
                "navigation.command", {"command": "next", "navigation_unit": "sentence", "expected_session_version": 1},
                request_id=nav_id))
            segments = [m["payload"] for m in first if m["type"] == "response.segment"]
            snapshots = [m["payload"] for m in first if m["type"] == "session.snapshot"]
            assert snapshots[-1]["session_version"] == 2
            assert snapshots[-1]["current_sentence_id"] == str(seeded.sentence_ids[0][1])
            assert segments and segments[0]["text"] == "It is written V = I × R."

            # Retransmission of the same logical command after a stale version: replayed, not re-applied.
            again = await client.settle(await client.send(
                "navigation.command", {"command": "next", "navigation_unit": "sentence", "expected_session_version": 1},
                request_id=nav_id))
            assert [m["payload"] for m in again if m["type"] == "response.segment"] == segments

            where = await client.settle(await client.send(
                "navigation.command", {"command": "where_am_i", "expected_session_version": 2}))
            assert all(m["type"] != "error" for m in where)

            turn = await client.until(await client.send("turn.submit", {
                "utterance": "Why is resistance constant?", "input_mode": "keyboard", "transcript_status": "final",
                "expected_session_version": 2}), "response.segment")
            assert turn[-1]["type"] == "error" and turn[-1]["payload"]["code"] == "PROVIDER_UNAVAILABLE"

            # Another account's session id on this connection is refused.
            intruder = ProtocolClient(ws, uuid4())
            denied = await intruder.until(await intruder.send("session.resume", {"last_known_session_version": 0}),
                                          "session.snapshot")
            assert denied[-1]["payload"]["code"] == "AUTHORIZATION_DENIED"

        # Reconnect: canonical state survived the disconnect exactly.
        async with connect(f"ws://{host}/v1/ws", additional_headers=_bearer(seeded.token), proxy=None) as ws:
            client = ProtocolClient(ws, session_id)
            resumed = await client.until(await client.send("session.resume", {"last_known_session_version": 2}),
                                         "session.snapshot")
            assert resumed[-1]["payload"]["session_version"] == 2
            assert resumed[-1]["payload"]["current_sentence_id"] == str(seeded.sentence_ids[0][1])


async def test_resume_reconciles_a_question_the_learning_store_already_closed(database_url):
    seeded = await seed_account_and_source(database_url)
    async with running_app(_settings(database_url)) as host, httpx.AsyncClient(base_url=f"http://{host}") as http:
        session_id = UUID((await http.post("/v1/sessions", headers=_bearer(seeded.token))).json()["session_id"])

        # A question was answered and committed, but the crash lost the session update.
        engine = create_engine(database_url)
        try:
            sessions = create_session_factory(engine)
            auth = AuthContext(account_id=seeded.account_id, session_id=session_id, request_id=uuid4(),
                               issued_at=datetime.now(timezone.utc))
            store = PostgresLearningStore(sessions)
            question = await store.persist_pending(auth, ApprovedQuestion(
                question_id=f"q-{uuid4()}", question_version=1, concept_id="ohms-law", kind=QuestionKind.TRUE_FALSE,
                prompt="Is R constant?", options=[QuestionOption(option_id="t", text="True"),
                                                  QuestionOption(option_id="f", text="False")],
                answer_key=AnswerKey(correct_answer="t"), created_at=datetime.now(timezone.utc)))
            service = SessionService(PostgresSessionRepository(engine))
            ref = PendingQuestionRef(question_id=question.question_id, question_version=1)

            async def pend(state):
                return state.model_copy(update={"pending_question": ref, "interaction_mode": InteractionMode.QUIZ})

            await service.merge_internal(auth, pend)
            await store.commit_answer(auth, AssessmentAttempt(
                attempt_id=uuid4(), account_id=seeded.account_id, concept_id="ohms-law",
                question_id=question.question_id, question_version=1, answer=AnswerSubmission(final_text="True"),
                outcome=AttemptOutcome.CORRECT, hints_used=0, evaluated_by="grader",
                created_at=datetime.now(timezone.utc)))
        finally:
            await engine.dispose()

        async with connect(f"ws://{host}/v1/ws", additional_headers=_bearer(seeded.token), proxy=None) as ws:
            client = ProtocolClient(ws, session_id)
            frames = await client.settle(await client.send("session.resume", {"last_known_session_version": 1}))
            assert [m["type"] for m in frames] == ["session.snapshot"]  # no error, no revived question
            snapshot = frames[0]["payload"]
            assert snapshot["pending_question"] is None and snapshot["interaction_mode"] == "idle"
            assert snapshot["session_version"] == 2
            # Resuming again is now a plain, stable snapshot.
            frames = await client.settle(await client.send("session.resume", {"last_known_session_version": 2}))
            assert [m["payload"]["session_version"] for m in frames] == [2]
