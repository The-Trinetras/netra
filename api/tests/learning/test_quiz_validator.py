import pytest

from netra_api.learning.quiz.models import AnswerKey, QuestionDraft, QuestionKind, QuestionOption
from netra_api.learning.quiz.validator import QuestionValidationError, validate_question_draft


def _mc_draft(**overrides):
    defaults = dict(
        concept_id="concept-x",
        kind=QuestionKind.MULTIPLE_CHOICE,
        prompt="Which protocol is connection-oriented?",
        options=[QuestionOption(option_id="a", text="UDP"), QuestionOption(option_id="b", text="TCP")],
        answer_key=AnswerKey(correct_answer="b"),
    )
    defaults.update(overrides)
    return QuestionDraft(**defaults)


def test_valid_multiple_choice_draft_passes():
    validate_question_draft(_mc_draft())


def test_multiple_choice_without_options_is_rejected():
    with pytest.raises(QuestionValidationError):
        validate_question_draft(_mc_draft(options=[]))


def test_multiple_choice_with_duplicate_option_ids_is_rejected():
    draft = _mc_draft(options=[QuestionOption(option_id="a", text="UDP"), QuestionOption(option_id="a", text="TCP")])
    with pytest.raises(QuestionValidationError):
        validate_question_draft(draft)


def test_multiple_choice_correct_answer_must_reference_an_option():
    draft = _mc_draft(answer_key=AnswerKey(correct_answer="z"))
    with pytest.raises(QuestionValidationError):
        validate_question_draft(draft)


def test_free_response_without_rubric_is_rejected():
    draft = QuestionDraft(
        concept_id="concept-x",
        kind=QuestionKind.FREE_RESPONSE,
        prompt="Explain congestion control.",
        answer_key=AnswerKey(),
    )
    with pytest.raises(QuestionValidationError):
        validate_question_draft(draft)


def test_short_answer_with_rubric_passes():
    draft = QuestionDraft(
        concept_id="concept-x",
        kind=QuestionKind.SHORT_ANSWER,
        prompt="What does TCP stand for?",
        answer_key=AnswerKey(correct_answer="Transmission Control Protocol", rubric="Accept minor spelling errors."),
    )
    validate_question_draft(draft)


def test_empty_prompt_is_rejected():
    with pytest.raises(QuestionValidationError):
        validate_question_draft(_mc_draft(prompt="   "))
