from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from netra_api.learning.assessment.models import (
    AnswerSubmission,
    AssessmentAttempt,
    AttemptOutcome,
    LearningEventProposal,
    LearningStatus,
)


def _attempt(**overrides):
    defaults = dict(
        attempt_id=uuid4(),
        account_id=uuid4(),
        concept_id="concept-congestion-control",
        question_id="q-1",
        question_version=1,
        answer=AnswerSubmission(final_text="TCP slows down to avoid overwhelming the network."),
        outcome=AttemptOutcome.CORRECT,
        evaluated_by="tutor",
        created_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return AssessmentAttempt(**defaults)


def test_learning_status_values_match_handoff_contract():
    assert LearningStatus.NOT_ASSESSED == "not_assessed"
    assert LearningStatus.NEEDS_REVIEW == "needs_review"
    assert LearningStatus.DEVELOPING == "developing"
    assert LearningStatus.DEMONSTRATED_RECENTLY == "demonstrated_recently"


def test_answer_submission_was_corrected_reflects_correction():
    uncorrected = AnswerSubmission(final_text="answer")
    corrected = AnswerSubmission(original_transcript="ansewr", corrected_text="answer", final_text="answer")
    assert uncorrected.was_corrected is False
    assert corrected.was_corrected is True


def test_assessment_attempt_rejects_zero_question_version():
    with pytest.raises(ValidationError):
        _attempt(question_version=0)


def test_learning_event_proposal_has_no_status_field():
    proposal = LearningEventProposal(account_id=uuid4(), concept_id="concept-x", event_type="concept_exposed")
    assert not hasattr(proposal, "status")
    assert not hasattr(proposal, "mastery")
