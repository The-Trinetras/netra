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
from netra_api.learning.assessment.service import derive_attempt_id
from netra_api.learning.quiz.models import AnswerKey, ApprovedQuestion, QuestionDraft, QuestionKind, QuestionOption
from netra_api.learning.quiz.validator import QuestionValidationError, validate_question_draft
from netra_api.learning.tutor.agent import (
    TutorServices,
    build_turn_state,
    load_tutor_instruction,
    run_turn,
)
from netra_api.learning.tutor.providers.groq import TutorModelDecision
from netra_api.platform.auth_context import AuthContext
from netra_api.platform.errors import AuthorizationError, NetraError, TurnBudgetExceededError

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
    """Labelled double for LearningService.

    Mirrors the real replay rule rather than approximating it: attempt_id
    comes from the production derive_attempt_id, so a retransmitted turn
    (same request_id) finds its own committed attempt and a genuinely new
    turn does not. A double that minted a fresh id per call would make the
    replay tests pass for the wrong reason.
    """

    def __init__(self, history=(), history_available=True):
        self.proposals = []
        self.committed: dict[UUID, AssessmentAttempt] = {}
        self._history = list(history)
        self._history_available = history_available

    def propose_event(self, auth, proposal):
        self.proposals.append(proposal)
        attempt_id = derive_attempt_id(auth.request_id, proposal.question_id, proposal.question_version)
        existing = self.committed.get(attempt_id)
        if existing is not None:
            return existing
        attempt = AssessmentAttempt(
            attempt_id=attempt_id,
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
        self.committed[attempt_id] = attempt
        return attempt

    def find_committed_attempt(self, auth, question_id, question_version):
        return self.committed.get(derive_attempt_id(auth.request_id, question_id, question_version))

    def list_attempts_for_concepts(self, auth, concept_ids):
        if not self._history_available:
            raise NetraError("history service unavailable")
        wanted = set(concept_ids)
        return [attempt for attempt in self._history if attempt.concept_id in wanted]


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
    assert budget.tool_calls_used == 2  # evidence resolution + history read


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


def _approving_grounding(draft, evidence):
    """TEST-ONLY injection standing in for an approved grounding result.

    This does NOT implement grounding and does not bypass it in
    production: TutorServices defaults to the real fail-closed
    validate_question_for_approval (see the test above). It exists so the
    delivery behaviour *downstream* of grounding — persist-before-deliver,
    answer-key privacy, question identity — can be exercised while the
    M3/M4 grounding decision is still open. Any claim that grounding is
    integrated must not rest on these tests.
    """

    validate_question_draft(draft)  # real structural validation still runs


async def test_approved_check_persists_the_question_before_delivering_it():
    """learning.md: "Persist the pending question before delivering it to
    the client." A question the student was asked but the server cannot
    resolve later is exactly what that ordering prevents."""

    handoff = _live_handoff(mode="check_understanding")
    services = _services(grounding_validator=_approving_grounding)
    state, result = _run(handoff, services)
    result = await result

    assert result.status == "awaiting_student_answer"
    assert len(services.pending_questions.persisted) == 1
    persisted = services.pending_questions.persisted[0]
    assert result.pending_question_id == persisted.question_id
    assert [segment.kind for segment in result.public_segments] == ["question"]


async def test_approved_check_delivers_the_prompt_but_never_the_answer_key():
    handoff = _live_handoff(mode="check_understanding")
    draft = QuestionDraft(
        concept_id="concept-congestion-control",
        kind=QuestionKind.TRUE_FALSE,
        prompt="Does congestion control protect the whole network?",
        options=[QuestionOption(option_id="true", text="True"), QuestionOption(option_id="false", text="False")],
        answer_key=AnswerKey(correct_answer="true", rubric="SECRET-CHECK-RUBRIC"),
    )
    services = _services(
        quiz_generator=_FakeQuizGenerator(draft=draft),
        grounding_validator=_approving_grounding,
    )
    state, result = _run(handoff, services)
    result = await result

    delivered = result.public_segments[0].text
    assert delivered == draft.prompt
    assert "SECRET-CHECK-RUBRIC" not in delivered
    # The persisted record still carries the key server-side.
    assert services.pending_questions.persisted[0].answer_key.rubric == "SECRET-CHECK-RUBRIC"


async def test_approved_check_uses_a_stable_question_id_across_a_replayed_handoff():
    """A retransmitted handoff must re-persist the same question rather
    than minting a second one for the same check."""

    handoff = _live_handoff(mode="check_understanding")

    first = _services(grounding_validator=_approving_grounding)
    state_a, result_a = _run(handoff, first)
    result_a = await result_a

    second = _services(grounding_validator=_approving_grounding)
    state_b, result_b = _run(handoff, second)
    result_b = await result_b

    assert result_a.pending_question_id == result_b.pending_question_id


async def test_approved_check_still_rejects_a_structurally_invalid_draft():
    """The injected grounding result does not disable structural
    validation: a choice question whose key matches no option is refused.

    QuestionValidationError is a NetraError, so it becomes a bounded
    failed result rather than escaping — what matters is that nothing was
    persisted or delivered. The grounding NotImplementedError is
    deliberately not a NetraError and still propagates (see
    test_check_understanding_fails_closed_on_evidence_grounding), which is
    what keeps an unmade product decision distinguishable from a routine
    validation failure."""

    handoff = _live_handoff(mode="check_understanding")
    bad_draft = QuestionDraft(
        concept_id="concept-congestion-control",
        kind=QuestionKind.TRUE_FALSE,
        prompt="Does congestion control protect the whole network?",
        options=[QuestionOption(option_id="true", text="True")],
        answer_key=AnswerKey(correct_answer="nonexistent-option"),
    )
    services = _services(
        quiz_generator=_FakeQuizGenerator(draft=bad_draft),
        grounding_validator=_approving_grounding,
    )
    state, result = _run(handoff, services)
    result = await result

    assert result.status == "failed"
    assert result.public_segments == []  # nothing delivered
    assert services.pending_questions.persisted == []  # nothing persisted


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


# --- replay vs. a genuinely new attempt ------------------------------------


async def test_retransmitting_the_same_turn_replays_instead_of_committing_twice():
    """Same request_id = the same logical action retransmitted
    (message-flow.md). It must reuse its effect, not append a second
    attempt, and must not report a spurious failure just because
    propose_event already cleared the pending question."""

    handoff = _answer_handoff("True")
    services = _services(pending_questions=_FakePendingQuestions([_question()]))
    auth = _auth(handoff)

    first = await run_turn(build_turn_state(handoff, auth, _inherited_budget(handoff)), services)
    # Same auth (same request_id) = the client retransmitted the action.
    second = await run_turn(build_turn_state(handoff, auth, _inherited_budget(handoff)), services)

    assert first.status == "completed"
    assert second.status == "completed"  # not a failure
    assert len(services.learning_service.proposals) == 1  # committed once
    assert len(services.learning_service.committed) == 1
    assert (
        first.proposed_learning_events[0].attempt_id
        == second.proposed_learning_events[0].attempt_id
    )


async def test_a_genuinely_new_attempt_is_a_separate_record():
    """A different request_id is a different turn, so a second answer to
    the same question is its own attempt, not a replay."""

    services = _services(pending_questions=_FakePendingQuestions([_question()]))
    handoff = _answer_handoff("True")

    first_auth = _auth(handoff)
    second_auth = AuthContext(
        account_id=first_auth.account_id,
        session_id=first_auth.session_id,
        request_id=uuid4(),  # new logical action
        issued_at=datetime.now(timezone.utc),
    )

    await run_turn(build_turn_state(handoff, first_auth, _inherited_budget(handoff)), services)
    services.pending_questions._answered.discard("q-1")  # re-offered by the session
    await run_turn(build_turn_state(handoff, second_auth, _inherited_budget(handoff)), services)

    assert len(services.learning_service.committed) == 2  # two distinct attempts


async def test_replay_costs_no_model_call_or_second_grade():
    handoff = _answer_handoff("It protects the shared links")
    services = _services(
        pending_questions=_FakePendingQuestions(
            [_question(kind=QuestionKind.FREE_RESPONSE, options=[], answer_key=AnswerKey(rubric="r"))]
        ),
        provider=_FakeProvider(raw_text="CORRECT\nYes, exactly."),
    )
    auth = _auth(handoff)

    await run_turn(build_turn_state(handoff, auth, _inherited_budget(handoff)), services)
    calls_after_first = services.provider.calls
    await run_turn(build_turn_state(handoff, auth, _inherited_budget(handoff)), services)

    assert services.provider.calls == calls_after_first  # replay re-graded nothing


# --- hints and assistance attribution --------------------------------------


def _hint_handoff(utterance="Can you give me a hint?", hints_used=0):
    return _live_handoff(
        mode="continue_lesson",
        original_utterance=utterance,
        pending_question={"question_id": "q-1", "question_version": 1, "hints_used": hints_used},
    )


async def test_hint_keeps_the_question_pending_and_records_no_attempt():
    handoff = _hint_handoff()
    services = _services(
        pending_questions=_FakePendingQuestions([_question()]),
        provider=_FakeProvider(raw_text="Look at how much the voltage rises for each extra amp."),
    )
    state, result = _run(handoff, services)
    result = await result

    assert result.status == "awaiting_student_answer"
    assert [segment.kind for segment in result.public_segments] == ["hint"]
    assert result.pending_question_id == "q-1"  # same question, not a new one
    assert services.learning_service.proposals == []  # assistance is not an attempt


async def test_hint_is_attributed_to_the_pending_question_and_proposes_hint_used():
    handoff = _hint_handoff()
    services = _services(
        pending_questions=_FakePendingQuestions([_question()]),
        provider=_FakeProvider(raw_text="Think about the slope."),
    )
    state, result = _run(handoff, services)
    result = await result

    assert [event.event_type for event in result.proposed_learning_events] == ["hint_used"]
    assert result.proposed_learning_events[0].concept_id == "concept-congestion-control"
    assert result.pending_question_id == "q-1"


async def test_hint_prompt_never_receives_the_answer_key():
    secret = "SECRET-KEY-VALUE"
    handoff = _hint_handoff()
    services = _services(
        pending_questions=_FakePendingQuestions(
            [_question(answer_key=AnswerKey(correct_answer=secret, rubric="SECRET-RUBRIC"))]
        ),
        provider=_FakeProvider(raw_text="Consider the units."),
    )
    state, result = _run(handoff, services)
    await result

    assert services.provider.prompts, "the hint path must have called the provider"
    for prompt in services.provider.prompts:
        assert secret not in prompt
        assert "SECRET-RUBRIC" not in prompt


async def test_assisted_attempt_is_distinguishable_from_an_independent_one():
    """learning.md: "Preserve assisted versus independent attempts"."""

    independent = _services(pending_questions=_FakePendingQuestions([_question()]))
    state_a, result_a = _run(_answer_handoff("True", hints_used=0), independent)
    await result_a

    assisted = _services(pending_questions=_FakePendingQuestions([_question()]))
    state_b, result_b = _run(_answer_handoff("True", hints_used=3), assisted)
    await result_b

    assert independent.learning_service.proposals[0].hints_used == 0
    assert assisted.learning_service.proposals[0].hints_used == 3


async def test_hint_for_a_question_that_is_not_pending_fails_closed():
    handoff = _hint_handoff()
    services = _services(pending_questions=_FakePendingQuestions([]))
    state, result = _run(handoff, services)
    result = await result

    assert result.status == "failed"
    assert result.public_segments == []


# --- navigation, ambiguity and misrecognition ------------------------------


async def test_navigation_utterance_never_becomes_an_answer_or_grade():
    """Deterministic commands are routed before the Tutor, but if one
    reaches evaluate_answer it must not be graded as a response."""

    for utterance in ("next", "back to reading", "repeat"):
        services = _services(pending_questions=_FakePendingQuestions([_question()]))
        state, result = _run(_answer_handoff(utterance), services)
        result = await result

        assert result.status == "awaiting_student_answer", utterance
        assert services.learning_service.proposals == [], utterance


async def test_misrecognized_units_ask_for_clarification_rather_than_failing():
    """"two volts" is not one of the options; treating it as INCORRECT
    would make a transcription artefact into a wrong answer."""

    services = _services(pending_questions=_FakePendingQuestions([_question()]))
    state, result = _run(_answer_handoff("two volts"), services)
    result = await result

    assert result.status == "awaiting_student_answer"
    assert result.pending_question_id == "q-1"
    assert services.learning_service.proposals == []


# --- history selection -----------------------------------------------------


def _past_attempt(**overrides):
    defaults = dict(
        attempt_id=uuid4(),
        account_id=uuid4(),
        concept_id="concept-congestion-control",
        question_id="q-earlier",
        question_version=1,
        answer=AnswerSubmission(final_text="Because the receiver is slow"),
        outcome=AttemptOutcome.INCORRECT,
        hints_used=1,
        evaluated_by="tutor",
        created_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return AssessmentAttempt(**defaults)


async def test_earlier_answers_reach_the_lesson_prompt_as_facts():
    """A later turn must be able to use what the student actually answered
    before, not merely greet them by name."""

    services = _services(learning_service=_FakeLearningService(history=[_past_attempt()]))
    state, result = _run(_live_handoff(mode="explain"), services)
    await result

    prompt = services.provider.prompts[0]
    assert "Because the receiver is slow" in prompt  # the exact earlier answer
    assert "incorrect" in prompt
    assert "1 hint" in prompt  # assistance preserved alongside the answer


async def test_unavailable_history_is_not_reported_as_no_history():
    """learning.md: "An unavailable history service is not evidence of no
    history"."""

    services = _services(learning_service=_FakeLearningService(history_available=False))
    state, result = _run(_live_handoff(mode="explain"), services)
    result = await result

    prompt = services.provider.prompts[0]
    assert "could not be retrieved" in prompt
    assert "No earlier attempts are recorded" not in prompt
    assert result.status == "completed"  # unavailable history does not fail the lesson


async def test_absent_history_says_none_rather_than_unknown():
    services = _services(learning_service=_FakeLearningService(history=[]))
    state, result = _run(_live_handoff(mode="explain"), services)
    await result

    prompt = services.provider.prompts[0]
    assert "No earlier attempts are recorded" in prompt
    assert "could not be retrieved" not in prompt


async def test_history_never_carries_a_derived_mastery_label():
    services = _services(learning_service=_FakeLearningService(history=[_past_attempt()]))
    state, result = _run(_live_handoff(mode="explain"), services)
    await result

    prompt = services.provider.prompts[0]
    for label in ("not_assessed", "needs_review", "developing", "demonstrated_recently"):
        assert label not in prompt


# --- failure modes ---------------------------------------------------------


async def test_provider_failure_returns_a_bounded_result_without_provider_detail():
    class _FailingProvider:
        calls = 0
        prompts: list[str] = []

        async def decide(self, config, prompt):
            raise NetraError("groq said: quota exceeded for org_12345")

    services = _services(provider=_FailingProvider())
    state, result = _run(_live_handoff(mode="explain"), services)
    result = await result

    assert result.status == "failed"
    assert "quota exceeded" not in (result.decision_summary or "")
    assert "org_12345" not in (result.decision_summary or "")


async def test_authorization_denial_is_never_converted_into_a_result():
    class _DenyingLearningService(_FakeLearningService):
        def propose_event(self, auth, proposal):
            raise AuthorizationError("account mismatch")

    services = _services(
        pending_questions=_FakePendingQuestions([_question()]),
        learning_service=_DenyingLearningService(),
    )
    state, coro = _run(_answer_handoff("True"), services)

    with pytest.raises(AuthorizationError):
        await coro


async def test_cancellation_midway_stops_the_turn():
    handoff = _live_handoff(mode="explain")
    budget = _inherited_budget(handoff)

    class _CancellingResolver(_FakeResolver):
        def resolve(self, auth, evidence_ids, pinned_source_version_id=None):
            budget.cancel()  # e.g. STOP arrived while evidence was resolving
            return super().resolve(auth, evidence_ids, pinned_source_version_id)

    services = _services(evidence_resolver=_CancellingResolver())
    state, result = _run(handoff, services, budget=budget)
    result = await result

    assert result.status == "failed"
    assert services.provider.calls == 0  # no generation after cancellation


# --- consent: the Tutor never starts a quiz on its own ---------------------


async def test_explaining_never_switches_into_a_quiz_unprompted():
    """learning.md: "Offer additional depth and ask before switching into
    a quiz." A question is only ever delivered in check_understanding
    mode, which the Coordinator sends once the student has agreed."""

    services = _services()
    state, result = _run(_live_handoff(mode="explain"), services)
    result = await result

    assert result.pending_question_id is None
    assert [segment.kind for segment in result.public_segments] == ["explanation"]
    assert services.pending_questions.persisted == []


# --- Ohm's Law source fixture ----------------------------------------------

OHMS_SOURCE = REPO_ROOT / "evaluation" / "cases" / "ohms_law_source.json"


def _ohms_evidence() -> list[Evidence]:
    """Load the committed SOURCE fixture as resolved Evidence.

    This is source material, deliberately kept distinct from the
    provider-response doubles (_FakeProvider and friends) that stand in
    for model output. Source content lives in a committed data file so the
    AgentSpec's acceptance values cannot drift silently; model text is
    written inline in the test that needs it.
    """

    fixture = json.loads(OHMS_SOURCE.read_text())
    return [
        Evidence(
            evidence_id=item["evidence_id"],
            source_version_id=UUID(fixture["source_version_id"]),
            locator=item["locator"],
            text=item["text"],
            provenance=item["provenance"],
            trust=EvidenceTrust(item["trust"]),
        )
        for item in fixture["evidence"]
    ]


class _OhmsResolver:
    """Resolver double backed by the Ohm's Law source fixture."""

    def __init__(self):
        self._by_id = {item.evidence_id: item for item in _ohms_evidence()}

    def resolve(self, auth, evidence_ids, pinned_source_version_id=None):
        return [
            EvidenceResolution(evidence_id=eid, evidence=self._by_id.get(eid))
            if eid in self._by_id
            else EvidenceResolution(
                evidence_id=eid, rejection_reason=EvidenceRejectionReason.NOT_FOUND
            )
            for eid in evidence_ids
        ]


def _ohms_handoff(**overrides):
    defaults = dict(
        learning_goal="Read the current-voltage graph and connect it to V = I x R.",
        original_utterance="What does the graph in figure 4.2 show?",
        target_concept_ids=["concept-ohms-law"],
        evidence_refs=[
            {
                "evidence_id": "ev-ohm-graph",
                "source_version_id": "6f9c1b52-0d34-4a7e-9d21-7c4f5a2b8e10",
                "evidence_version": 1,
            },
            {
                "evidence_id": "ev-ohm-table",
                "source_version_id": "6f9c1b52-0d34-4a7e-9d21-7c4f5a2b8e10",
                "evidence_version": 1,
            },
            {
                "evidence_id": "ev-ohm-equation",
                "source_version_id": "6f9c1b52-0d34-4a7e-9d21-7c4f5a2b8e10",
                "evidence_version": 1,
            },
        ],
    )
    defaults.update(overrides)
    return _live_handoff(**defaults)


async def test_ohms_law_explanation_grounds_in_the_source_fixture():
    services = _services(evidence_resolver=_OhmsResolver())
    state, result = _run(_ohms_handoff(mode="explain"), services)
    result = await result

    prompt = services.provider.prompts[0]
    assert "1 A, 2 V" in prompt and "2 A, 4 V" in prompt and "3 A, 6 V" in prompt
    assert "V = I x R" in prompt
    assert "current on the horizontal x-axis" in prompt
    assert set(result.evidence_ids) == {"ev-ohm-graph", "ev-ohm-table", "ev-ohm-equation"}


async def test_copied_number_answer_is_recorded_verbatim_with_its_verdict():
    """The student answers a resistance question with "6" — a value copied
    straight out of the voltage column rather than derived via R = V / I.

    The verdict here comes from a labelled provider double, not from real
    model judgment: this test pins that the copied answer is preserved
    exactly as given and attributed to the right question, not that the
    Tutor is good at spotting copied numbers.
    """

    question = _question(
        concept_id="concept-ohms-law",
        kind=QuestionKind.SHORT_ANSWER,
        options=[],
        prompt="Using table 4.1, what is the resistance in ohms?",
        answer_key=AnswerKey(correct_answer="2", rubric="Must divide voltage by current."),
    )
    services = _services(
        evidence_resolver=_OhmsResolver(),
        pending_questions=_FakePendingQuestions([question]),
        provider=_FakeProvider(
            raw_text="INCORRECT\nThat is a voltage from the table. Divide a voltage by its current."
        ),
    )
    handoff = _ohms_handoff(
        mode="evaluate_answer",
        original_utterance="6",
        pending_question={"question_id": "q-1", "question_version": 1, "hints_used": 0},
    )
    state, result = _run(handoff, services)
    result = await result

    proposal = services.learning_service.proposals[0]
    assert proposal.answer.final_text == "6"  # verbatim, not normalised
    assert proposal.outcome == AttemptOutcome.INCORRECT
    assert proposal.question_id == "q-1"
    assert proposal.concept_id == "concept-ohms-law"
    assert "2" not in result.public_segments[0].text.split()  # key not restated as a bare value


# --- the runtime instruction is actually loaded ----------------------------


def test_tutor_instruction_loads_from_the_prompt_file():
    instruction = load_tutor_instruction()
    assert "Netra's Tutor" in instruction
    assert "NOT_AN_ANSWER" in instruction  # the grading contract the parser expects


async def test_services_load_the_instruction_by_default_into_the_prompt():
    """A Tutor running with an empty instruction would silently lose every
    grounding and answer-key rule, so the default must not be blank."""

    services = TutorServices(
        provider=_FakeProvider(),
        evidence_resolver=_FakeResolver(),
        pending_questions=_FakePendingQuestions(),
        learning_service=_FakeLearningService(),
    )
    assert "Netra's Tutor" in services.instruction

    state, result = _run(_live_handoff(mode="explain"), services)
    await result
    assert "Netra's Tutor" in services.provider.prompts[0]


async def test_commit_uses_the_authenticated_account_not_the_handoff():
    """CLAUDE.md: "An account ID ... supplied by a model/client is not
    authority"."""

    handoff = _answer_handoff("True")
    services = _services(pending_questions=_FakePendingQuestions([_question()]))
    state = build_turn_state(handoff, _auth(handoff), _inherited_budget(handoff))
    await run_turn(state, services)

    assert services.learning_service.proposals[0].account_id == state.auth.account_id
