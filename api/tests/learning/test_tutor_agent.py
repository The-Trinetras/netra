import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from netra_api.content.retrieval.evidence import (
    Evidence,
    EvidenceRejectionReason,
    EvidenceResolution,
    EvidenceTrust,
)
from netra_api.coordinator.handoff import CoordinatorToTutorHandoff
from netra_api.coordinator.limits import TurnBudget
from netra_api.learning.assessment.models import AssessmentAttempt, AnswerSubmission, AttemptOutcome
from netra_api.learning.quiz.models import AnswerKey, ApprovedQuestion, QuestionDraft, QuestionKind, QuestionOption
from netra_api.learning.tutor.agent import TutorServices, build_turn_state, run_turn
from netra_api.learning.tutor.providers.groq import TutorModelDecision
from netra_api.platform.auth_context import AuthContext
from netra_api.platform.errors import TurnBudgetExceededError

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_HANDOFF = REPO_ROOT / "shared" / "contracts" / "examples" / "handoffs" / "coordinator_to_tutor.json"


def _load_handoff(deadline_at: datetime | None = None, **overrides) -> CoordinatorToTutorHandoff:
    """Parse the committed example, optionally with a live deadline.

    The example pins deadline_at to a fixed past timestamp, which is right
    for a contract fixture but useless for budget behaviour: any budget
    faithful to it has already expired. Tests that exercise the budget
    move the deadline forward and change nothing else.
    """

    data = json.loads(EXAMPLE_HANDOFF.read_text())
    if deadline_at is not None:
        data["deadline_at"] = deadline_at.isoformat()
    data.update(overrides)
    return CoordinatorToTutorHandoff.model_validate(data)


def _live_handoff(**overrides) -> CoordinatorToTutorHandoff:
    return _load_handoff(deadline_at=datetime.now(timezone.utc) + timedelta(seconds=20), **overrides)


def _auth(handoff: CoordinatorToTutorHandoff) -> AuthContext:
    return AuthContext(
        account_id=uuid4(),
        session_id=handoff.session_id,
        request_id=handoff.request_id,
        issued_at=datetime.now(timezone.utc),
    )


def _inherited_budget(handoff: CoordinatorToTutorHandoff) -> TurnBudget:
    """The budget a Coordinator would hand down: it expires when the
    originating turn does, not 20 seconds from whenever the Tutor starts."""

    return TurnBudget.from_deadline(handoff.deadline_at)


# --- test doubles ----------------------------------------------------------


class _FakeProvider:
    """Labelled test double. Never a real Groq call — no network here."""

    def __init__(self, raw_text="Congestion control protects the shared network.", finish_reason="stop"):
        self.raw_text = raw_text
        self.finish_reason = finish_reason
        self.calls = 0
        self.prompts: list[str] = []

    async def decide(self, config, prompt):
        self.calls += 1
        self.prompts.append(prompt)
        return TutorModelDecision(raw_text=self.raw_text, finish_reason=self.finish_reason)


class _FakeResolver:
    def __init__(self, evidence=None, reject=False):
        self._evidence = evidence
        self.reject = reject

    def resolve(self, auth, evidence_ids, pinned_source_version_id=None):
        if self.reject:
            return [
                EvidenceResolution(evidence_id=eid, rejection_reason=EvidenceRejectionReason.UNAUTHORIZED)
                for eid in evidence_ids
            ]
        return [
            EvidenceResolution(
                evidence_id=eid,
                evidence=self._evidence
                or Evidence(
                    evidence_id=eid,
                    source_version_id=uuid4(),
                    locator="p. 41",
                    text="Congestion control limits the sending rate to protect the network.",
                    provenance="textbook chapter 4",
                    trust=EvidenceTrust.SOURCE_VERIFIED,
                ),
            )
            for eid in evidence_ids
        ]


class _FakePendingQuestions:
    def __init__(self, pending=()):
        self._pending = {q.question_id: q for q in pending}
        self._answered: set[str] = set()
        self.persisted: list[ApprovedQuestion] = []

    def persist_pending(self, auth, question):
        self.persisted.append(question)
        self._pending[question.question_id] = question
        return question

    def get_pending(self, auth, question_id):
        if question_id in self._answered:
            return None
        return self._pending.get(question_id)

    def mark_answered(self, auth, question_id, question_version):
        self._answered.add(question_id)


class _FakeLearningService:
    def __init__(self):
        self.proposals = []

    def propose_event(self, auth, proposal):
        self.proposals.append(proposal)
        return AssessmentAttempt(
            attempt_id=UUID("11111111-2222-3333-4444-555555555555"),
            account_id=proposal.account_id,
            concept_id=proposal.concept_id,
            question_id=proposal.question_id,
            question_version=proposal.question_version,
            answer=proposal.answer,
            outcome=proposal.outcome,
            hints_used=proposal.hints_used,
            evaluated_by=proposal.evaluated_by,
            created_at=datetime.now(timezone.utc),
        )


class _FakeQuizGenerator:
    def __init__(self, draft=None):
        self.draft = draft or QuestionDraft(
            concept_id="concept-congestion-control",
            kind=QuestionKind.TRUE_FALSE,
            prompt="Does congestion control protect the network as a whole?",
            options=[QuestionOption(option_id="true", text="True"), QuestionOption(option_id="false", text="False")],
            answer_key=AnswerKey(correct_answer="true"),
        )

    async def generate(self, request):
        return self.draft


def _services(**overrides) -> TutorServices:
    defaults = dict(
        provider=_FakeProvider(),
        evidence_resolver=_FakeResolver(),
        pending_questions=_FakePendingQuestions(),
        learning_service=_FakeLearningService(),
        quiz_generator=_FakeQuizGenerator(),
        instruction="TEST INSTRUCTION",
    )
    defaults.update(overrides)
    return TutorServices(**defaults)


def _question(**overrides) -> ApprovedQuestion:
    defaults = dict(
        question_id="q-1",
        question_version=1,
        concept_id="concept-congestion-control",
        kind=QuestionKind.TRUE_FALSE,
        prompt="Does congestion control protect the network as a whole?",
        options=[QuestionOption(option_id="true", text="True"), QuestionOption(option_id="false", text="False")],
        answer_key=AnswerKey(correct_answer="true"),
        created_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return ApprovedQuestion(**defaults)


def _run(handoff, services, budget=None):
    state = build_turn_state(handoff, _auth(handoff), budget or _inherited_budget(handoff))
    return state, run_turn(state, services)


# --- build_turn_state (unchanged behaviour) --------------------------------


def test_build_turn_state_wraps_handoff_and_budget():
    handoff = _live_handoff()
    budget = _inherited_budget(handoff)
    state = build_turn_state(handoff, _auth(handoff), budget)
    assert state.handoff is handoff
    assert state.budget is budget


def test_build_turn_state_rejects_a_restarted_budget():
    """CLAUDE.md: "Retries, fallback and delegated work consume the
    originating turn's budget." A fresh TurnBudget() starts its own 20
    seconds, which runs past the originating turn's deadline."""

    handoff = _load_handoff(deadline_at=datetime.now(timezone.utc) + timedelta(seconds=5))
    with pytest.raises(TurnBudgetExceededError):
        build_turn_state(handoff, _auth(handoff), TurnBudget())


def test_delegated_budget_shares_the_coordinator_counters():
    """Sharing the instance is what makes tool calls spent by the Tutor
    count against the originating turn."""

    handoff = _live_handoff()
    budget = _inherited_budget(handoff)
    budget.register_tool_call()

    state = build_turn_state(handoff, _auth(handoff), budget)
    state.budget.register_tool_call()

    assert budget.tool_calls_used == 2


# --- explain / continue_lesson ---------------------------------------------


async def test_explain_answers_from_resolved_evidence():
    handoff = _live_handoff(mode="explain")
    services = _services()
    state, result = _run(handoff, services)
    result = await result

    assert result.status == "completed"
    assert result.handoff_id == handoff.handoff_id
    assert [segment.kind for segment in result.public_segments] == ["explanation"]
    assert result.evidence_ids == ["ev-27"]
    assert result.pending_question_id is None


async def test_explain_spends_the_shared_budget():
    handoff = _live_handoff(mode="explain")
    budget = _inherited_budget(handoff)
    state, result = _run(handoff, _services(), budget=budget)
    await result

    assert budget.model_decisions_used == 1  # one explanation decision
    assert budget.tool_calls_used == 1  # one evidence resolution


async def test_explain_reports_needs_more_evidence_when_nothing_resolves():
    """An unresolved gap is stated, never filled in with invented detail."""

    handoff = _live_handoff(mode="explain")
    services = _services(evidence_resolver=_FakeResolver(reject=True))
    state, result = _run(handoff, services)
    result = await result

    assert result.status == "needs_more_evidence"
    assert result.public_segments == []
    assert services.provider.calls == 0  # no model call on an evidence gap


async def test_explain_proposes_exposure_without_any_mastery_claim():
    handoff = _live_handoff(mode="explain")
    services = _services()
    state, result = _run(handoff, services)
    result = await result

    assert [event.event_type for event in result.proposed_learning_events] == ["concept_exposed"]
    # concept_exposed is reported on the wire only; it is never committed,
    # because AssessmentAttempt cannot represent non-answer activity.
    assert services.learning_service.proposals == []
    for event in result.proposed_learning_events:
        assert not hasattr(event, "status")


async def test_exhausted_budget_returns_a_bounded_failure_not_an_exception():
    handoff = _live_handoff(mode="explain")
    budget = _inherited_budget(handoff)
    budget.cancel()

    state, result = _run(handoff, _services(), budget=budget)
    result = await result

    assert result.status == "failed"
    assert result.public_segments == []


# --- check_understanding ---------------------------------------------------


async def test_check_understanding_fails_closed_on_evidence_grounding():
    """validate_draft_is_grounded is deliberately unimplemented pending an
    M3/M4 product decision; nothing may be persisted or delivered until it
    exists."""

    handoff = _live_handoff(mode="check_understanding")
    services = _services()
    state, coro = _run(handoff, services)

    with pytest.raises(NotImplementedError):
        await coro

    assert services.pending_questions.persisted == []


# --- evaluate_answer -------------------------------------------------------


def _answer_handoff(utterance, hints_used=0, **overrides):
    return _live_handoff(
        mode="evaluate_answer",
        original_utterance=utterance,
        pending_question={"question_id": "q-1", "question_version": 1, "hints_used": hints_used},
        **overrides,
    )


async def test_correct_choice_answer_is_graded_without_a_model_call():
    handoff = _answer_handoff("True")
    services = _services(pending_questions=_FakePendingQuestions([_question()]))
    state, result = _run(handoff, services)
    result = await result

    assert result.status == "completed"
    assert services.provider.calls == 0  # deterministic kinds need no model
    assert len(services.learning_service.proposals) == 1
    proposal = services.learning_service.proposals[0]
    assert proposal.outcome == AttemptOutcome.CORRECT
    assert proposal.evaluated_by == "grader"
    assert result.proposed_learning_events[0].attempt_id is not None


async def test_incorrect_choice_answer_records_an_attempt():
    handoff = _answer_handoff("False")
    services = _services(pending_questions=_FakePendingQuestions([_question()]))
    state, result = _run(handoff, services)
    result = await result

    assert services.learning_service.proposals[0].outcome == AttemptOutcome.INCORRECT
    assert result.public_segments[0].kind == "correction"


async def test_unmatched_utterance_asks_for_clarification_and_records_nothing():
    """docs/team/M4.md: "misrecognized units, transcript correction ...
    correction is not automatically failure"."""

    handoff = _answer_handoff("wait, I meant the other thing")
    services = _services(pending_questions=_FakePendingQuestions([_question()]))
    state, result = _run(handoff, services)
    result = await result

    assert result.status == "awaiting_student_answer"
    assert result.pending_question_id == "q-1"  # unchanged, not regenerated
    assert services.learning_service.proposals == []  # no grade for a non-answer


async def test_empty_utterance_records_nothing():
    handoff = _answer_handoff("   ")
    services = _services(pending_questions=_FakePendingQuestions([_question()]))
    state, result = _run(handoff, services)
    result = await result

    assert result.status == "awaiting_student_answer"
    assert services.learning_service.proposals == []


async def test_declined_check_records_no_grade():
    """A free-response decline: the model reports NOT_AN_ANSWER, so the
    check records untested study rather than a wrong answer."""

    handoff = _answer_handoff("I'd rather keep reading for now")
    services = _services(
        pending_questions=_FakePendingQuestions(
            [_question(kind=QuestionKind.FREE_RESPONSE, options=[], answer_key=AnswerKey(rubric="Mentions shared network."))]
        ),
        provider=_FakeProvider(raw_text="NOT_AN_ANSWER\nNo problem, we can come back to it."),
    )
    state, result = _run(handoff, services)
    result = await result

    assert result.status == "awaiting_student_answer"
    assert result.pending_question_id == "q-1"
    assert services.learning_service.proposals == []


async def test_free_response_answer_is_graded_against_the_rubric():
    handoff = _answer_handoff("It stops senders from swamping the shared links", hints_used=2)
    services = _services(
        pending_questions=_FakePendingQuestions(
            [_question(kind=QuestionKind.FREE_RESPONSE, options=[], answer_key=AnswerKey(rubric="Mentions shared network."))]
        ),
        provider=_FakeProvider(raw_text="CORRECT\nExactly — it protects the shared path, not just the receiver."),
    )
    state, result = _run(handoff, services)
    result = await result

    assert result.status == "completed"
    proposal = services.learning_service.proposals[0]
    assert proposal.outcome == AttemptOutcome.CORRECT
    assert proposal.evaluated_by == "tutor"
    assert proposal.hints_used == 2  # per-attempt assistance preserved
    # The verdict token is never spoken to the student.
    assert "CORRECT" not in result.public_segments[0].text


async def test_rubric_feedback_cites_the_evidence_it_was_grounded_in():
    handoff = _answer_handoff("It protects the shared links")
    services = _services(
        pending_questions=_FakePendingQuestions(
            [_question(kind=QuestionKind.FREE_RESPONSE, options=[], answer_key=AnswerKey(rubric="Mentions shared network."))]
        ),
        provider=_FakeProvider(raw_text="CORRECT\nThat's it exactly."),
    )
    state, result = _run(handoff, services)
    result = await result

    assert result.evidence_ids == ["ev-27"]


async def test_choice_grading_cites_no_evidence_it_never_read():
    """A choice answer is graded against the stored key, not evidence, so
    reporting an evidence id here would be a citation nothing supports."""

    handoff = _answer_handoff("True")
    services = _services(pending_questions=_FakePendingQuestions([_question()]))
    state, result = _run(handoff, services)
    result = await result

    assert result.evidence_ids == []


async def test_unparseable_grading_output_fails_closed():
    handoff = _answer_handoff("something about routers")
    services = _services(
        pending_questions=_FakePendingQuestions(
            [_question(kind=QuestionKind.FREE_RESPONSE, options=[], answer_key=AnswerKey(rubric="Mentions shared network."))]
        ),
        provider=_FakeProvider(raw_text="I think that's roughly right, well done"),
    )
    state, result = _run(handoff, services)
    result = await result

    assert result.status == "awaiting_student_answer"
    assert services.learning_service.proposals == []  # never infer a grade from prose


async def test_answer_key_never_reaches_a_public_segment():
    secret = "SECRET-REFERENCE-ANSWER"
    handoff = _answer_handoff("it protects the network")
    services = _services(
        pending_questions=_FakePendingQuestions(
            [
                _question(
                    kind=QuestionKind.FREE_RESPONSE,
                    options=[],
                    answer_key=AnswerKey(correct_answer=secret, rubric="SECRET-RUBRIC"),
                )
            ]
        ),
        provider=_FakeProvider(raw_text="CORRECT\nThat's the idea."),
    )
    state, result = _run(handoff, services)
    result = await result

    for segment in result.public_segments:
        assert secret not in segment.text
        assert "SECRET-RUBRIC" not in segment.text


async def test_evaluate_answer_rejects_a_question_that_is_not_pending():
    handoff = _answer_handoff("True")
    services = _services(pending_questions=_FakePendingQuestions([]))
    state, result = _run(handoff, services)
    result = await result

    assert result.status == "failed"
    assert services.learning_service.proposals == []


async def test_evaluate_answer_rejects_a_stale_question_version():
    handoff = _answer_handoff("True")
    services = _services(pending_questions=_FakePendingQuestions([_question(question_version=3)]))
    state, result = _run(handoff, services)
    result = await result

    assert result.status == "failed"
    assert services.learning_service.proposals == []


async def test_waiting_for_an_answer_consumes_no_further_model_calls():
    """integration-checklist.md: "wait without model calls". The turn ends
    when the Tutor is waiting; there is no polling loop."""

    handoff = _answer_handoff("wait, I meant something else")
    services = _services(pending_questions=_FakePendingQuestions([_question()]))
    budget = _inherited_budget(handoff)
    state, result = _run(handoff, services, budget=budget)
    result = await result

    assert result.status == "awaiting_student_answer"
    assert budget.model_decisions_used == 0
    assert services.provider.calls == 0


async def test_commit_uses_the_authenticated_account_not_the_handoff():
    """CLAUDE.md: "An account ID ... supplied by a model/client is not
    authority"."""

    handoff = _answer_handoff("True")
    services = _services(pending_questions=_FakePendingQuestions([_question()]))
    state = build_turn_state(handoff, _auth(handoff), _inherited_budget(handoff))
    await run_turn(state, services)

    assert services.learning_service.proposals[0].account_id == state.auth.account_id
