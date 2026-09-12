"""A student answers in their own words, not in option ids.

The audit found grade_objective_answer comparing AnswerSubmission.final_text
straight against an option_id, which marks every spoken multiple-choice
answer wrong.
"""

from datetime import datetime, timezone

from netra_api.learning.assessment.grader import grade_objective_answer, resolve_submitted_option
from netra_api.learning.assessment.models import AnswerSubmission, AttemptOutcome
from netra_api.learning.quiz.models import AnswerKey, ApprovedQuestion, QuestionKind, QuestionOption


def _question():
    return ApprovedQuestion(
        question_id="q-1",
        question_version=1,
        concept_id="concept-x",
        kind=QuestionKind.MULTIPLE_CHOICE,
        prompt="Which protocol is connection-oriented?",
        options=[
            QuestionOption(option_id="opt-1", text="UDP"),
            QuestionOption(option_id="opt-2", text="TCP"),
        ],
        answer_key=AnswerKey(correct_answer="opt-2"),
        created_at=datetime.now(timezone.utc),
    )


def test_spoken_option_text_is_graded_correct():
    """The regression: "TCP" is the right answer and must not be marked
    wrong just because it is not the string "opt-2"."""

    outcome = grade_objective_answer(_question(), AnswerSubmission(final_text="TCP"))
    assert outcome == AttemptOutcome.CORRECT


def test_spoken_option_text_is_case_and_whitespace_insensitive():
    outcome = grade_objective_answer(_question(), AnswerSubmission(final_text="  tcp "))
    assert outcome == AttemptOutcome.CORRECT


def test_wrong_option_text_is_graded_incorrect():
    outcome = grade_objective_answer(_question(), AnswerSubmission(final_text="UDP"))
    assert outcome == AttemptOutcome.INCORRECT


def test_option_id_still_grades_correctly():
    outcome = grade_objective_answer(_question(), AnswerSubmission(final_text="opt-2"))
    assert outcome == AttemptOutcome.CORRECT


def test_answer_matching_no_option_is_incorrect_not_partial():
    outcome = grade_objective_answer(_question(), AnswerSubmission(final_text="carrier pigeon"))
    assert outcome == AttemptOutcome.INCORRECT


def test_resolution_reports_no_match_rather_than_guessing():
    """No positional or letter-label matching: which of those a student can
    use is an unmade product decision."""

    assert resolve_submitted_option(_question(), "the second one") is None
    assert resolve_submitted_option(_question(), "b") is None


def test_corrected_transcript_is_graded_on_its_final_text():
    answer = AnswerSubmission(original_transcript="teasy pee", corrected_text="TCP", final_text="TCP")
    assert grade_objective_answer(_question(), answer) == AttemptOutcome.CORRECT
