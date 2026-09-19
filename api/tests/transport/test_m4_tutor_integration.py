"""M1 transport + Coordinator driving M4's MERGED Tutor code (run_turn/build_turn_state).

Real code under test: M1 transport/session/engine/gateway, M4's
``netra_api.learning.tutor.agent.run_turn`` and M4's real ``LearningService``.
Labelled doubles: the Groq provider (scripted text, no network), the
assessment-history repository (in-memory), the pending-question repository,
M2/M3 services from ohm_fixture and the scripted Coordinator model.
The StatusDerivationPolicy values below exist only because the legacy
constructor requires them; they are test values, not product policy.
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from netra_api.coordinator.limits import MAX_MODEL_DECISIONS_PER_TURN
from netra_api.coordinator.tutor_gateway import LearningTutorRunner
from netra_api.learning.assessment.service import LearningService, StatusDerivationPolicy
from netra_api.learning.quiz.models import AnswerKey, ApprovedQuestion, QuestionKind, QuestionOption
from netra_api.learning.tutor.agent import CHECK_SKIPPED_TEXT, TutorServices
from netra_api.learning.tutor.providers.groq import TutorModelDecision
from netra_api.session.modes import InteractionMode
from netra_api.session.state import ActiveLessonRef, PendingQuestionRef
from netra_api.transport.websocket.endpoint import serve

from ohm_fixture import (
    AXES,
    SESSION,
    FakeSocket,
    FixturePendingQuestions,
    FixtureResolver,
    ScriptedModel,
    build_journey,
    envelope,
    final,
    fid,
    initial_state,
    tools,
    wait_for,
)


class ScriptedGroq:
    """Labelled double for M4's GroqTutorProvider. No network."""

    def __init__(self, text: str):
        self.text = text
        self.prompts: list[str] = []

    async def decide(self, config, prompt):
        self.prompts.append(prompt)
        return TutorModelDecision(raw_text=self.text, finish_reason="stop")


class InMemoryAttempts:
    """Labelled in-memory AssessmentHistoryRepository double (M4/M2 persistence pending)."""

    def __init__(self):
        self.attempts = {}

    def append(self, auth, attempt):
        self.attempts.setdefault(attempt.attempt_id, attempt)
        return self.attempts[attempt.attempt_id]

    def list_for_concept(self, auth, concept_id):
        return [a for a in self.attempts.values() if a.concept_id == concept_id and a.account_id == auth.account_id]

    def list_for_account(self, auth):
        return [a for a in self.attempts.values() if a.account_id == auth.account_id]

    def get(self, auth, attempt_id):
        return self.attempts.get(attempt_id)


class CapturingRunner(LearningTutorRunner):
    """Real M4 run_turn; records the budget instance it was handed."""

    budgets = []

    async def run(self, state):
        result = await super().run(state)
        self.budgets.append(state.budget)
        return result


class RecordingSink:
    def __init__(self):
        self.forwarded = []

    async def forward(self, auth, handoff_id, result):
        self.forwarded.append(result)


Q_MC = ApprovedQuestion(
    question_id="q-mc",
    question_version=1,
    concept_id="ohms-law",
    kind=QuestionKind.MULTIPLE_CHOICE,
    prompt="For this resistor, what voltage corresponds to 4 amperes?",
    options=[QuestionOption(option_id="opt-a", text="4 volts"), QuestionOption(option_id="opt-b", text="8 volts")],
    answer_key=AnswerKey(correct_answer="opt-b"),
    created_at=datetime(2026, 9, 17, tzinfo=timezone.utc),
)


def _m4_services(pending, provider):
    attempts = InMemoryAttempts()
    learning = LearningService(
        attempts,
        StatusDerivationPolicy(policy_version="test-only", recent_window=timedelta(days=1), recent_correct_required=1),
        pending,
    )
    services = TutorServices(
        provider=provider,
        evidence_resolver=FixtureResolver(),
        pending_questions=pending,
        learning_service=learning,
        instruction="test instruction",
    )
    return services, attempts


async def _journey(model, *, state=None, pending=None, groq_text="Each row is 2 volts per ampere, so resistance is 2 ohms."):
    pending = pending or FixturePendingQuestions()
    provider = ScriptedGroq(groq_text)
    services, attempts = _m4_services(pending, provider)
    journey = await build_journey(model=model, state=state, tutor=False)
    # Register M4's real runner exactly as bootstrap does for IntegrationDependencies.tutor_services.
    from netra_api.coordinator.tutor_gateway import TutorGateway

    sink = RecordingSink()
    journey.services.coordinator._tutor = TutorGateway(
        CapturingRunner(services), pending_questions=pending, dialogue=journey.dialogue, learning_sink=sink
    )
    journey.services.navigator._pending_questions = pending
    return journey, provider, attempts, pending, sink


async def _open(journey):
    socket = FakeSocket()
    task = asyncio.ensure_future(serve(socket, journey.services, journey.composition.verifier))
    await wait_for(lambda: socket.accepted)
    return socket, task


async def _submit(socket, message, until="response.segment"):
    socket.push(message)
    rid = message["request_id"]
    await wait_for(lambda: any(m["request_id"] == rid and m["type"] in (until, "error") for m in socket.texts), 3)
    return [m for m in socket.texts if m["request_id"] == rid]


def _turn(utterance, version, request_id=None):
    return envelope("turn.submit", {"utterance": utterance, "input_mode": "keyboard", "transcript_status": "final", "expected_session_version": version}, request_id=request_id)


async def test_real_m4_explanation_runs_on_the_coordinators_budget():
    model = ScriptedModel(
        [
            tools(("search_sources", {"query": "table rows"}), requirements=[{"requirement_id": "rows", "description": "table rows"}]),
            final(
                {
                    "action": "delegate_to_tutor",
                    "assessments": [{"requirement_id": "rows", "status": "supported", "evidence_id": "ev-table-tbl01"}],
                    "tutor": {"mode": "explain", "learning_goal": "Explain the constant ratio.", "target_concept_ids": ["ohms-law"], "evidence_ids": ["ev-table-tbl01"]},
                }
            ),
        ]
    )
    journey, provider, attempts, _, sink = await _journey(model)
    socket, task = await _open(journey)
    responses = await _submit(socket, _turn("Why is the resistance constant?", 10))

    segment = next(m for m in responses if m["type"] == "response.segment")["payload"]
    assert segment["text"] == "Each row is 2 volts per ampere, so resistance is 2 ohms."
    assert segment["evidence_ids"] == ["ev-table-tbl01"]
    assert "(1 A, 2 V)" in provider.prompts[0]  # M4 resolved the handed evidence itself
    assert attempts.attempts == {}  # explanation commits no attempt
    handoff_result = [e.detail for e in journey.trace.of_kind("handoff_result") if "status" in e.detail][0]
    assert handoff_result["status"] == "completed"
    # 2 Coordinator decisions + 1 M4 decision; 1 Coordinator tool + M4's evidence
    # resolution and history selection, all on the ONE originating budget.
    budget = journey.services.coordinator._tutor._runner.budgets[-1]
    assert (budget.model_decisions_used, budget.tool_calls_used) == (3, 3)
    assert budget.model_decisions_used <= MAX_MODEL_DECISIONS_PER_TURN and len(provider.prompts) == 1
    state = await journey.sessions.get(SESSION)
    assert state.interaction_mode == InteractionMode.TUTOR_LESSON and state.active_lesson is not None
    socket.disconnect()
    await task


async def test_real_m4_answer_commits_once_and_retransmission_does_not_recommit():
    pending = FixturePendingQuestions()
    pending.questions[Q_MC.question_id] = Q_MC
    lesson = fid("lesson")
    state = initial_state(
        interaction_mode=InteractionMode.TUTOR_LESSON,
        active_lesson=ActiveLessonRef(lesson_id=lesson),
        pending_question=PendingQuestionRef(question_id="q-mc", question_version=1, hints_used=0),
    )

    def script():
        return [
            tools(("search_sources", {"query": "table rows"}), requirements=[{"requirement_id": "rows", "description": "table rows"}]),
            final(
                {
                    "action": "delegate_to_tutor",
                    "assessments": [{"requirement_id": "rows", "status": "supported", "evidence_id": "ev-table-tbl01"}],
                    "tutor": {"mode": "evaluate_answer", "learning_goal": "Evaluate the answer.", "target_concept_ids": ["ohms-law"], "evidence_ids": ["ev-table-tbl01"]},
                }
            ),
        ]

    model = ScriptedModel(script())
    journey, provider, attempts, pending, sink = await _journey(model, state=state, pending=pending)
    socket, task = await _open(journey)
    request_id = uuid4()
    first = await _submit(socket, _turn("8 volts", 10, request_id))

    assert len(attempts.attempts) == 1
    attempt = next(iter(attempts.attempts.values()))
    assert attempt.answer.final_text == "8 volts" and attempt.hints_used == 0
    assert provider.prompts == []  # choice grading made no model call
    assert sink.forwarded == [] or all(not e.attempt_id for r in sink.forwarded for e in r.proposed_learning_events)
    committed_notes = [e.detail for e in journey.trace.of_kind("handoff_result") if "already_committed_attempts" in e.detail]
    assert committed_notes == [{"already_committed_attempts": 1}]
    assert (await journey.sessions.get(SESSION)).pending_question is None

    socket.texts.clear()
    replay = await _submit(socket, _turn("8 volts", 10, request_id))
    assert [m["payload"] for m in replay] == [m["payload"] for m in first]
    assert len(attempts.attempts) == 1 and model.calls == 2
    socket.disconnect()
    await task


def _check_script():
    return ScriptedModel(
        [
            tools(("search_sources", {"query": "table rows"}), requirements=[{"requirement_id": "rows", "description": "table rows"}]),
            final(
                {
                    "action": "delegate_to_tutor",
                    "assessments": [{"requirement_id": "rows", "status": "supported", "evidence_id": "ev-table-tbl01"}],
                    "tutor": {"mode": "check_understanding", "learning_goal": "Offer an optional check.", "target_concept_ids": ["ohms-law"], "evidence_ids": ["ev-table-tbl01"]},
                }
            ),
        ]
    )


class _Draft:
    """Labelled QuizGenerator double; ``evidence_ids`` is what the draft cites."""

    def __init__(self, evidence_ids, supported=False):
        self.evidence_ids = evidence_ids
        self.supported = supported

    async def generate(self, request):
        from netra_api.learning.quiz.models import QuestionDraft

        if self.supported:  # the table states 4 V at 2 A
            return QuestionDraft(
                concept_id="ohms-law",
                kind=QuestionKind.MULTIPLE_CHOICE,
                prompt="What voltage does the table show at 2 A?",
                options=[QuestionOption(option_id="a", text="4 V"), QuestionOption(option_id="b", text="6 V")],
                answer_key=AnswerKey(correct_answer="a"),
                evidence_ids=self.evidence_ids,
            )
        return QuestionDraft(
            concept_id="ohms-law",
            kind=QuestionKind.TRUE_FALSE,
            prompt="Is R constant?",
            options=[QuestionOption(option_id="true", text="True"), QuestionOption(option_id="false", text="False")],
            answer_key=AnswerKey(correct_answer="true"),
            evidence_ids=self.evidence_ids,
        )


async def _check_journey(evidence_ids, supported=False):
    journey, provider, attempts, pending, _ = await _journey(_check_script())
    gateway = journey.services.coordinator._tutor
    gateway._runner.services = TutorServices(**{**gateway._runner.services.__dict__, "quiz_generator": _Draft(evidence_ids, supported)})
    socket, task = await _open(journey)
    responses = await _submit(socket, _turn("Can you check my understanding?", 10))
    return journey, pending, socket, task, responses


async def test_m4_supported_check_is_persisted_and_delivered():
    # P-1: the table states the answer, so the real validator approves it.
    journey, pending, socket, task, responses = await _check_journey(["ev-table-tbl01"], supported=True)
    questions = [m for m in responses if m["type"] == "quiz.question"]
    assert len(questions) == 1 and questions[0]["payload"]["prompt"] == "What voltage does the table show at 2 A?"
    assert "4 V" not in json.dumps(questions[0]["payload"].get("answer_key", ""))
    assert len(pending.questions) == 1
    assert (await journey.sessions.get(SESSION)).pending_question is not None
    socket.disconnect()
    await task


async def test_m4_unsupported_check_is_skipped_and_said_plainly():
    # The draft cites resolved evidence, but the table never says R is
    # constant, so P-1 skips it: said plainly, nothing persisted or pending.
    journey, pending, socket, task, responses = await _check_journey(["ev-table-tbl01"])
    assert journey.trace.of_kind("handoff_rejected") == []
    assert CHECK_SKIPPED_TEXT in json.dumps(responses)
    assert not [m for m in responses if m["type"] == "quiz.question"]
    assert pending.questions == {}
    assert (await journey.sessions.get(SESSION)).pending_question is None
    socket.disconnect()
    await task


async def test_m4_uncited_or_unresolved_draft_is_refused_by_binding():
    # Reference binding runs before the support check: a draft citing
    # nothing, or citing evidence this turn never resolved, is skipped like
    # an unsupported one, and nothing may be persisted or delivered.
    for cited in ([], ["ev-not-handed-to-this-turn"]):
        journey, pending, socket, task, responses = await _check_journey(cited, supported=True)
        assert journey.trace.of_kind("handoff_rejected") == []
        statuses = [e.detail["status"] for e in journey.trace.of_kind("handoff_result") if "status" in e.detail]
        assert statuses == ["completed"], cited
        assert CHECK_SKIPPED_TEXT in json.dumps(responses)
        assert not [m for m in responses if m["type"] == "quiz.question"]
        assert pending.questions == {}
        assert (await journey.sessions.get(SESSION)).pending_question is None
        socket.disconnect()
        await task
