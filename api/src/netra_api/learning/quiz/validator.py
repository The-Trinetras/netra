"""Quiz validation — a bounded tool, not the Tutor.

CLAUDE.md "Architecture: only two agents" explicitly lists "quiz
validation" as NOT an agent: it is deterministic, structural checking of
a Tutor-proposed QuestionDraft, run before the draft may become an
ApprovedQuestion (see netra_api.learning.quiz.repository). It never
judges whether a *student's* answer is correct — that is either
netra_api.learning.assessment.grader (deterministic, objective kinds)
or the Tutor itself (rubric-graded free text). This is pure, local
validation with no LLM call and no I/O, so it is implemented in full
rather than stubbed.
"""

from __future__ import annotations

from netra_api.content.retrieval.evidence import Evidence
from netra_api.learning.quiz.models import QuestionDraft, QuestionKind
from netra_api.platform.errors import NetraError

CHOICE_KINDS = frozenset({QuestionKind.MULTIPLE_CHOICE, QuestionKind.TRUE_FALSE})
RUBRIC_REQUIRED_KINDS = frozenset({QuestionKind.SHORT_ANSWER, QuestionKind.FREE_RESPONSE})


class QuestionValidationError(NetraError):
    """Raised when a QuestionDraft is not well-formed enough to approve."""


def validate_question_draft(draft: QuestionDraft) -> None:
    """Raise QuestionValidationError if draft is not ready to be approved.

    Checks are purely structural: presence of a prompt, unique options
    with a correct_answer that references one of them for choice kinds,
    and a grading rubric for kinds the Tutor must grade by judgment.
    """

    if not draft.prompt.strip():
        raise QuestionValidationError("prompt must not be empty")

    if draft.kind in CHOICE_KINDS:
        if not draft.options:
            raise QuestionValidationError(f"{draft.kind.value} requires at least one option")
        option_ids = [option.option_id for option in draft.options]
        if len(option_ids) != len(set(option_ids)):
            raise QuestionValidationError("option_id values must be unique")
        if draft.answer_key.correct_answer not in option_ids:
            raise QuestionValidationError(
                f"{draft.kind.value} answer_key.correct_answer must reference an option_id"
            )

    if draft.kind in RUBRIC_REQUIRED_KINDS and not draft.answer_key.rubric:
        raise QuestionValidationError(f"{draft.kind.value} requires answer_key.rubric for Tutor grading")


def validate_draft_is_grounded(draft: QuestionDraft, evidence: list[Evidence]) -> None:
    """Check that a draft question and its answer key are supported by evidence.

    learning.md: "Validate that questions and reference answers are
    supported by the selected evidence." Structural validation cannot do
    this. A draft can be perfectly well-formed and still ask about
    something the source never said, or carry a reference answer the
    evidence contradicts, and approving it would put a fabricated claim
    in front of a student as an assessable fact.

    Left unimplemented deliberately. What counts as "supported" is a
    product decision spanning M3 and M4 that has not been made: whether
    grounding is checked by entailment against the evidence text, by a
    Tutor self-check, or by requiring a human-reviewed question bank.
    Each implies a different pipeline, and guessing one would make it
    policy by default.

    Fails closed: until that decision exists, nothing can be approved
    through this path (CLAUDE.md: "Unimplemented authorization or
    persistence must fail closed, never return success").
    """

    raise NotImplementedError(
        "TODO: quiz evidence grounding — no approved definition of evidence support exists yet"
    )


def validate_question_for_approval(draft: QuestionDraft, evidence: list[Evidence]) -> None:
    """Run every check a draft must pass before it may become an ApprovedQuestion.

    Structural checks first, then grounding. Callers must use this rather
    than validate_question_draft alone: a structurally valid question is
    not an approvable one.
    """

    validate_question_draft(draft)
    validate_draft_is_grounded(draft, evidence)
