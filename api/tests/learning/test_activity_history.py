"""Factual activity records (D3) — PROPOSED shape, in-memory doubles only.

These pin the proposal's semantics for M1/M2/M5 review: facts only, no
label fields, generated distinct from delivered, replay-safe ids, per
attempt assistance and correction provenance. They prove no persistence;
the repository is a labelled double and no migration exists.
"""

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from netra_api.learning.assessment.models import AnswerSubmission, AssessmentAttempt, AttemptOutcome
from netra_api.learning.history.models import (
    ActivityKind,
    ActivityProposal,
    ActivityRecord,
    DeliveryFact,
    DeliveryStage,
    derive_activity_id,
)
from netra_api.learning.history.service import (
    ActivityHistoryService,
    ActivityReferenceError,
    ActivityReplayConflictError,
)
from netra_api.learning.quiz.models import AnswerKey, ApprovedQuestion, QuestionKind
from netra_api.platform.auth_context import AuthContext
from netra_api.platform.errors import AuthorizationError

LESSON = uuid4()


class _Records:
    def __init__(self):
        self.by_id: dict[UUID, ActivityRecord] = {}

    def append(self, auth, record):
        return self.by_id.setdefault(record.activity_id, record)

    def get(self, auth, activity_id):
        record = self.by_id.get(activity_id)
        if record is not None and record.account_id != auth.account_id:
            raise AuthorizationError("not yours")
        return record

    def list_for_concepts(self, auth, concept_ids):
        wanted = set(concept_ids)
        return [
            r for r in self.by_id.values()
            if r.account_id == auth.account_id and wanted & set(r.concept_ids)
        ]


class _Attempts:
    def __init__(self, attempts=()):
        self.by_id = {a.attempt_id: a for a in attempts}

    def get(self, auth, attempt_id):
        attempt = self.by_id.get(attempt_id)
        if attempt is not None and attempt.account_id != auth.account_id:
            raise AuthorizationError("not yours")
        return attempt


class _Pending:
    def __init__(self, questions=()):
        self.by_id = {q.question_id: q for q in questions}

    def get_pending(self, auth, question_id):
        return self.by_id.get(question_id)


def _auth(account_id=None, request_id=None):
    return AuthContext(
        account_id=account_id or uuid4(),
        session_id=uuid4(),
        request_id=request_id or uuid4(),
        issued_at=datetime.now(timezone.utc),
    )


def _question():
    return ApprovedQuestion(
        question_id="q-1",
        question_version=1,
        concept_id="concept-ohms-law",
        kind=QuestionKind.SHORT_ANSWER,
        prompt="What is the resistance?",
        answer_key=AnswerKey(correct_answer="2", rubric="R = V / I"),
        created_at=datetime.now(timezone.utc),
    )


def _attempt(account_id):
    return AssessmentAttempt(
        attempt_id=uuid4(),
        account_id=account_id,
        concept_id="concept-ohms-law",
        question_id="q-1",
        question_version=1,
        answer=AnswerSubmission(final_text="6"),
        outcome=AttemptOutcome.INCORRECT,
        hints_used=1,
        evaluated_by="tutor",
        created_at=datetime.now(timezone.utc),
    )


def _service(attempts=(), pending=(_question(),)):
    return ActivityHistoryService(_Records(), _Attempts(attempts), _Pending(pending))


def _proposal(**overrides):
    defaults = dict(
        kind=ActivityKind.EXPLANATION_GENERATED,
        lesson_id=LESSON,
        concept_ids=["concept-ohms-law"],
        text="Voltage rises in proportion to current.",
        evidence_ids=["ev-ohm-table"],
    )
    defaults.update(overrides)
    return ActivityProposal(**defaults)


def test_an_explanation_is_recorded_as_generated_under_the_authenticated_identity():
    auth = _auth()
    record = _service().record(auth, _proposal())

    assert record.kind is ActivityKind.EXPLANATION_GENERATED
    assert (record.account_id, record.session_id, record.request_id) == (
        auth.account_id, auth.session_id, auth.request_id,
    )


def test_no_record_or_delivery_fact_has_a_label_or_untested_status_field():
    forbidden = {"status", "mastery", "misconception", "understanding", "level", "score"}
    for model in (ActivityProposal, ActivityRecord, DeliveryFact):
        assert not forbidden & set(model.model_fields)
    assert not any("tested" in kind.value or "studied" in kind.value for kind in ActivityKind)


def test_generated_is_not_delivered_and_delivery_stages_are_separate_facts():
    assert "delivered" not in {kind.value for kind in ActivityKind}
    assert [stage.value for stage in DeliveryStage] == ["sent", "played", "acknowledged"]
    assert "stage" not in ActivityRecord.model_fields


def test_a_retransmitted_turn_reuses_the_same_record():
    service = _service()
    auth = _auth()
    first = service.record(auth, _proposal())
    second = service.record(auth, _proposal())

    assert first.activity_id == second.activity_id
    assert first.activity_id == derive_activity_id(auth.request_id, ActivityKind.EXPLANATION_GENERATED, 0)


def test_the_same_identity_with_different_content_is_refused_not_replaced():
    service = _service()
    auth = _auth()
    service.record(auth, _proposal())
    with pytest.raises(ActivityReplayConflictError):
        service.record(auth, _proposal(text="Something else entirely."))


def test_a_hint_is_attributed_to_the_pending_question_not_an_attempt():
    record = _service().record(
        _auth(),
        _proposal(kind=ActivityKind.HINT_GENERATED, question_id="q-1", question_version=1,
                  text="Divide a voltage by its current."),
    )
    assert (record.question_id, record.attempt_id) == ("q-1", None)

    with pytest.raises(ValidationError):
        _proposal(kind=ActivityKind.HINT_GENERATED, question_id="q-1", question_version=1,
                  attempt_id=uuid4())


def test_a_hint_for_a_question_that_is_not_pending_is_refused():
    with pytest.raises(ActivityReferenceError):
        _service(pending=()).record(
            _auth(),
            _proposal(kind=ActivityKind.HINT_GENERATED, question_id="q-1", question_version=1),
        )


def test_feedback_and_stated_reasoning_belong_to_a_committed_attempt_of_this_account():
    auth = _auth()
    attempt = _attempt(auth.account_id)
    service = _service(attempts=[attempt])

    feedback = service.record(auth, _proposal(
        kind=ActivityKind.FEEDBACK_GENERATED, attempt_id=attempt.attempt_id,
        question_id="q-1", question_version=1, text="That is a voltage from the table."))
    reasoning = service.record(auth, _proposal(
        kind=ActivityKind.STUDENT_REASONING_STATED, attempt_id=attempt.attempt_id,
        evidence_ids=[], text="I took the biggest number in the voltage column."))

    assert feedback.attempt_id == reasoning.attempt_id == attempt.attempt_id
    assert reasoning.text == "I took the biggest number in the voltage column."  # verbatim


def test_references_to_missing_or_foreign_attempts_are_refused():
    auth = _auth()
    with pytest.raises(ActivityReferenceError):
        _service().record(auth, _proposal(kind=ActivityKind.FEEDBACK_GENERATED, attempt_id=uuid4()))

    foreign = _attempt(uuid4())
    with pytest.raises(AuthorizationError):
        _service(attempts=[foreign]).record(
            auth, _proposal(kind=ActivityKind.FEEDBACK_GENERATED, attempt_id=foreign.attempt_id))


def test_a_transcript_correction_keeps_the_original_and_is_not_an_attempt():
    record = _service().record(_auth(), _proposal(
        kind=ActivityKind.TRANSCRIPT_CORRECTED, evidence_ids=[],
        original_text="two homes", text="two ohms"))

    assert (record.original_text, record.text) == ("two homes", "two ohms")
    assert record.attempt_id is None

    with pytest.raises(ValidationError):
        _proposal(kind=ActivityKind.TRANSCRIPT_CORRECTED, evidence_ids=[], text="two ohms")
    with pytest.raises(ValidationError):
        _proposal(kind=ActivityKind.TRANSCRIPT_CORRECTED, evidence_ids=[],
                  original_text="same", text="same")


def test_generated_teaching_content_must_cite_evidence():
    for kind in (ActivityKind.EXPLANATION_GENERATED, ActivityKind.FEEDBACK_GENERATED):
        with pytest.raises(ValidationError):
            _proposal(kind=kind, evidence_ids=[], attempt_id=uuid4())


def test_identity_cannot_be_supplied_by_the_producer():
    with pytest.raises(ValidationError):
        ActivityProposal(**{**_proposal().model_dump(), "account_id": str(uuid4())})
