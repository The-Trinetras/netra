"""TutorRunner — the M4 adapter M1's composition calls (INT-09).

Every collaborator here is a labelled in-memory double from
test_tutor_agent. These tests prove the adapter's semantics, not M1's
Session integration or any persistence: that needs M1's published
Session service and M2's repositories (docs/team/handoffs/M4.md).
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from netra_api.coordinator.limits import TurnBudget
from netra_api.learning.tutor.runner import (
    PendingQuestionChange,
    TutorCapabilityPendingError,
    TutorRunner,
    UndeliverableQuestionError,
)
from netra_api.platform.auth_context import AuthContext
from netra_api.platform.errors import AuthorizationError, TurnBudgetExceededError

from test_tutor_agent import (
    _FakePendingQuestions,
    _FakeProvider,
    _answer_handoff,
    _approving_grounding,
    _auth,
    _inherited_budget,
    _live_handoff,
    _load_handoff,
    _question,
    _services,
)


async def _run(handoff, services, budget=None, auth=None):
    budget = budget or _inherited_budget(handoff)
    return await TutorRunner(services).run(handoff, auth or _auth(handoff), budget), budget


async def test_the_runner_spends_the_originating_budget_instance():
    handoff = _live_handoff(mode="explain")
    budget = _inherited_budget(handoff)
    budget.register_model_decision()  # the Coordinator already made one decision

    outcome, budget = await _run(handoff, _services(), budget=budget)

    assert outcome.result.status == "completed"
    assert budget.model_decisions_used == 2  # Coordinator's one plus the Tutor's one
    assert budget.tool_calls_used >= 1


async def test_a_fresh_budget_that_outlives_the_handoff_is_rejected():
    handoff = _live_handoff(mode="explain")
    with pytest.raises(TurnBudgetExceededError):
        await TutorRunner(_services()).run(
            handoff, _auth(handoff), TurnBudget(deadline_seconds=3600)
        )


async def test_a_handoff_for_another_session_is_refused_before_any_work():
    handoff = _live_handoff(mode="explain")
    services = _services()
    other_session = AuthContext(
        account_id=uuid4(),
        session_id=uuid4(),
        request_id=handoff.request_id,
        issued_at=datetime.now(timezone.utc),
    )
    with pytest.raises(AuthorizationError):
        await _run(handoff, services, auth=other_session)
    assert services.provider.calls == 0


async def test_an_explanation_changes_no_pending_question_and_commits_nothing():
    outcome, _ = await _run(_live_handoff(mode="explain"), _services())

    assert outcome.pending_question.change is PendingQuestionChange.NONE
    assert outcome.committed_events == ()
    assert [e.event_type for e in outcome.uncommitted_events] == ["concept_exposed"]


async def test_an_answer_commit_is_reported_as_committed_and_clears_the_question():
    services = _services(pending_questions=_FakePendingQuestions([_question()]))
    outcome, _ = await _run(_answer_handoff("True"), services)

    (event,) = outcome.committed_events
    assert event.event_type == "answer_evaluated" and event.attempt_id is not None
    assert outcome.uncommitted_events == ()
    assert outcome.pending_question.change is PendingQuestionChange.ANSWERED


async def test_a_retransmitted_answer_reports_the_same_committed_attempt_once():
    """attempt_id means already committed: the replayed turn reports the
    same id and the Learning service is not asked to commit again."""

    services = _services(pending_questions=_FakePendingQuestions([_question()]))
    handoff = _answer_handoff("True")
    auth = _auth(handoff)

    first, _ = await _run(handoff, services, auth=auth)
    second, _ = await _run(handoff, services, auth=auth)

    assert first.committed_events[0].attempt_id == second.committed_events[0].attempt_id
    assert len(services.learning_service.proposals) == 1
    assert len(services.learning_service.committed) == 1
    assert second.pending_question.change is PendingQuestionChange.ANSWERED


async def test_a_hint_keeps_the_question_and_reports_one_more_hint():
    services = _services(pending_questions=_FakePendingQuestions([_question()]))
    handoff = _live_handoff(
        mode="continue_lesson",
        pending_question={"question_id": "q-1", "question_version": 1, "hints_used": 1},
    )
    outcome, _ = await _run(handoff, services)

    pending = outcome.pending_question
    assert pending.change is PendingQuestionChange.HINT_GIVEN
    assert (pending.question_id, pending.question_version, pending.hints_used) == ("q-1", 1, 2)
    assert outcome.committed_events == ()
    assert [e.event_type for e in outcome.uncommitted_events] == ["hint_used"]


async def test_an_unrecognised_answer_leaves_the_question_exactly_as_it_was():
    services = _services(pending_questions=_FakePendingQuestions([_question()]))
    outcome, _ = await _run(_answer_handoff("hmm, not sure", hints_used=2), services)

    pending = outcome.pending_question
    assert pending.change is PendingQuestionChange.UNCHANGED
    assert (pending.question_id, pending.question_version, pending.hints_used) == ("q-1", 1, 2)
    assert services.provider.calls == 0  # waiting consumes no model call


async def test_a_new_question_is_reported_only_once_it_is_persisted():
    services = _services(grounding_validator=_approving_grounding)
    outcome, _ = await _run(_live_handoff(mode="check_understanding"), services)

    pending = outcome.pending_question
    persisted = services.pending_questions.persisted[0]
    assert pending.change is PendingQuestionChange.NEW_QUESTION
    assert pending.question_id == persisted.question_id
    assert pending.question_version == persisted.question_version
    assert pending.hints_used == 0


async def test_a_new_question_the_store_cannot_resolve_is_never_handed_over():
    class _LosingStore(_FakePendingQuestions):
        def get_pending(self, auth, question_id):
            return None  # the write did not survive

    services = _services(
        pending_questions=_LosingStore(), grounding_validator=_approving_grounding
    )
    with pytest.raises(UndeliverableQuestionError):
        await _run(_live_handoff(mode="check_understanding"), services)


async def test_an_unimplemented_capability_is_still_a_typed_error():
    """Any NotImplementedError from a Tutor path reaches M1 as a NetraError
    it can map, distinct from a failed turn, and nothing is persisted."""

    def _unimplemented(draft, evidence):
        raise NotImplementedError("pending")

    services = _services(grounding_validator=_unimplemented)
    with pytest.raises(TutorCapabilityPendingError):
        await _run(_live_handoff(mode="check_understanding"), services)
    assert services.pending_questions.persisted == []


async def test_a_cancellation_during_the_turn_is_flagged_for_the_caller():
    handoff = _live_handoff(mode="explain")
    budget = _inherited_budget(handoff)

    class _CancellingProvider(_FakeProvider):
        async def decide(self, config, prompt):
            budget.cancel()  # STOP arrived while the model was generating
            return await super().decide(config, prompt)

    outcome, _ = await _run(handoff, _services(provider=_CancellingProvider()), budget=budget)

    assert outcome.cancelled_during_turn is True


async def test_an_expired_originating_turn_yields_a_bounded_failure_not_a_restart():
    """The originating deadline already passed: the Tutor does no work and
    the runner does not grant it a new allowance."""

    handoff = _load_handoff(
        deadline_at=datetime.now(timezone.utc) - timedelta(seconds=1), mode="explain"
    )
    services = _services()
    outcome, _ = await _run(handoff, services, budget=TurnBudget.from_deadline(handoff.deadline_at))

    assert outcome.result.status == "failed"
    assert services.provider.calls == 0
