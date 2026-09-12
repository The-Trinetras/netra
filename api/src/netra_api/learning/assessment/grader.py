"""Deterministic objective-answer grading — a bounded tool, not the Tutor.

CLAUDE.md "Architecture: only two agents" does not list grading itself,
but "quiz validation" is explicitly a bounded, non-agent workflow, and
the same reasoning applies here: for question kinds where correctness is
unambiguous from the stored answer key (multiple choice, true/false),
grading needs no judgment and therefore no LLM call. Free-text answers
that require judgment against a rubric are the Tutor's job (CLAUDE.md
"Tutor": "evaluating an answer against an approved question/rubric") —
this grader deliberately refuses any other question kind rather than
guessing.

Grading always evaluates AnswerSubmission.final_text; a corrected
transcript is graded like any other final answer, never penalized for
having been corrected (CLAUDE.md "Tutor learning rules": "Corrected
speech/transcript should not automatically count as failure").
"""

from __future__ import annotations

from typing import Optional, Protocol

from netra_api.learning.assessment.models import AnswerSubmission, AttemptOutcome
from netra_api.learning.quiz.models import ApprovedQuestion, QuestionKind
from netra_api.platform.errors import NetraError

DETERMINISTIC_QUESTION_KINDS = frozenset({QuestionKind.MULTIPLE_CHOICE, QuestionKind.TRUE_FALSE})
"""Kinds netra_api.learning.quiz.validator guarantees carry a
correct_answer referencing one of the question's options."""


class UngradeableAnswerError(NetraError):
    """Raised when a question's kind requires Tutor judgment, not deterministic grading."""

    def __init__(self, question_kind: QuestionKind) -> None:
        self.question_kind = question_kind
        super().__init__(f"question kind {question_kind.value!r} is not deterministically gradeable")


class ObjectiveGrader(Protocol):
    """Typed contract for grading a deterministic-kind question."""

    def grade(self, question: ApprovedQuestion, answer: AnswerSubmission) -> AttemptOutcome:
        ...


def resolve_submitted_option(
    question: ApprovedQuestion, submitted_text: str
) -> Optional[str]:
    """Map what the student actually said or typed onto an option_id.

    For MULTIPLE_CHOICE and TRUE_FALSE, answer_key.correct_answer holds
    an option_id, while AnswerSubmission.final_text holds the student's
    own words. Comparing those two directly never matches: a student
    saying "true" is not going to utter an internal identifier. This
    resolves the utterance to an option first.

    Matching is exact, after case and whitespace folding, against each
    option's option_id and its display text. Nothing looser: positional
    phrasing ("the second one"), letter labels and partial matches all
    depend on how options are presented to the student, which is an
    unmade product decision. Guessing one here would silently mark real
    answers wrong or right.

    Returns the matching option_id, or None when the answer matches no
    option.
    """

    normalized = submitted_text.strip().casefold()
    if not normalized:
        return None

    for option in question.options:
        if normalized == option.option_id.strip().casefold():
            return option.option_id
        if normalized == option.text.strip().casefold():
            return option.option_id

    return None


def grade_objective_answer(question: ApprovedQuestion, answer: AnswerSubmission) -> AttemptOutcome:
    """Grade answer.final_text against question.answer_key.correct_answer.

    Raises UngradeableAnswerError for any question.kind outside
    DETERMINISTIC_QUESTION_KINDS; callers must route those to the Tutor
    instead.

    An answer that resolves to no option is INCORRECT rather than PARTIAL
    or an error: it is simply not the correct answer, and treating an
    unrecognised response as partially right would be a grading policy
    nobody has approved.
    """

    if question.kind not in DETERMINISTIC_QUESTION_KINDS:
        raise UngradeableAnswerError(question.kind)

    submitted_option_id = resolve_submitted_option(question, answer.final_text)
    if submitted_option_id is None:
        return AttemptOutcome.INCORRECT

    correct_option_id = (question.answer_key.correct_answer or "").strip()
    return (
        AttemptOutcome.CORRECT
        if submitted_option_id == correct_option_id
        else AttemptOutcome.INCORRECT
    )
