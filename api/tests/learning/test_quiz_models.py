from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from netra_api.learning.quiz.models import (
    AnswerKey,
    ApprovedQuestion,
    QuestionKind,
    QuestionOption,
    StudentFacingQuestion,
)


def _approved_question():
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


def test_student_facing_question_never_carries_an_answer_key():
    student_question = StudentFacingQuestion.from_approved(_approved_question())
    assert not hasattr(student_question, "answer_key")
    assert "answer_key" not in student_question.model_dump()


def test_student_facing_question_preserves_options_and_prompt():
    student_question = StudentFacingQuestion.from_approved(_approved_question())
    assert student_question.prompt == "Which protocol is connection-oriented?"
    assert [option.option_id for option in student_question.options] == ["a", "b"]


def test_approved_question_rejects_zero_version():
    with pytest.raises(ValidationError):
        ApprovedQuestion(
            question_id="q-1",
            question_version=0,
            concept_id="concept-x",
            kind=QuestionKind.TRUE_FALSE,
            prompt="TCP is connectionless.",
            answer_key=AnswerKey(correct_answer="false"),
            created_at=datetime.now(timezone.utc),
        )
