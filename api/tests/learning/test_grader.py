from datetime import datetime, timezone

import pytest

from netra_api.learning.assessment.grader import UngradeableAnswerError, grade_objective_answer
from netra_api.learning.assessment.models import AnswerSubmission, AttemptOutcome
from netra_api.learning.quiz.models import AnswerKey, ApprovedQuestion, QuestionKind, QuestionOption


def _multiple_choice_question():
    return ApprovedQuestion(
        question_id="q-1",
        question_version=1,
        concept_id="concept-x",
        kind=QuestionKind.MULTIPLE_CHOICE,
        prompt="Which protocol is connection-oriented?",
        options=[QuestionOption(option_id="a", text="UDP"), QuestionOption(option_id="b", text="TCP")],
        answer_key=AnswerKey(correct_answer="b"),
        created_at=datetime.now(timezone.utc),
    )


def _free_response_question():
    return ApprovedQuestion(
        question_id="q-2",
        question_version=1,
        concept_id="concept-x",
        kind=QuestionKind.FREE_RESPONSE,
        prompt="Explain congestion control.",
        answer_key=AnswerKey(rubric="Mentions network protection, not just receiver protection."),
        created_at=datetime.now(timezone.utc),
    )


def test_grade_objective_answer_correct_option():
    outcome = grade_objective_answer(_multiple_choice_question(), AnswerSubmission(final_text="b"))
    assert outcome == AttemptOutcome.CORRECT


def test_grade_objective_answer_is_case_and_whitespace_insensitive():
    outcome = grade_objective_answer(_multiple_choice_question(), AnswerSubmission(final_text="  B  "))
    assert outcome == AttemptOutcome.CORRECT


def test_grade_objective_answer_incorrect_option():
    outcome = grade_objective_answer(_multiple_choice_question(), AnswerSubmission(final_text="a"))
    assert outcome == AttemptOutcome.INCORRECT


def test_grade_objective_answer_grades_corrected_transcript_normally():
    answer = AnswerSubmission(original_transcript="bee", corrected_text="b", final_text="b")
    assert grade_objective_answer(_multiple_choice_question(), answer) == AttemptOutcome.CORRECT


def test_grade_objective_answer_refuses_free_response():
    with pytest.raises(UngradeableAnswerError):
        grade_objective_answer(_free_response_question(), AnswerSubmission(final_text="anything"))
