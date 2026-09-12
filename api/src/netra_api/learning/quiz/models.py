"""Quiz/question domain models.

CLAUDE.md "Tutor learning rules": "Quiz answers/private answer keys must
not be sent to the client before the student's answer is finalized."
That rule is enforced structurally here, the same way
netra_api.coordinator.policies enforces Coordinator restrictions by
omission: StudentFacingQuestion has no answer_key field at all, so there
is no attribute a transport-layer bug could accidentally serialize to
the client. ApprovedQuestion (which does carry the answer key) is an
internal, server-side-only shape.

Quiz generation is the Tutor's job (CLAUDE.md "Tutor": "proposing
quizzes" — see netra_api.learning.quiz.generator); quiz *validation* is
an explicitly-listed bounded, non-agent workflow (CLAUDE.md
"Architecture: only two agents") — see
netra_api.learning.quiz.validator. This module only fixes the shapes
both depend on.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class QuestionKind(str, Enum):
    MULTIPLE_CHOICE = "multiple_choice"
    TRUE_FALSE = "true_false"
    SHORT_ANSWER = "short_answer"
    FREE_RESPONSE = "free_response"


class QuestionOption(BaseModel):
    option_id: str
    text: str


class AnswerKey(BaseModel):
    """The correct answer and/or grading rubric. Server-side only.

    correct_answer is an option_id for MULTIPLE_CHOICE/TRUE_FALSE, or
    the expected text for SHORT_ANSWER; it is None for FREE_RESPONSE,
    which is graded by the Tutor against rubric instead (CLAUDE.md
    "Tutor": "evaluating an answer against an approved question/
    rubric"). netra_api.learning.quiz.validator enforces which
    combination is required per QuestionKind before a draft may be
    approved.
    """

    correct_answer: Optional[str] = None
    rubric: Optional[str] = None


class QuestionDraft(BaseModel):
    """A quiz question the Tutor has proposed but not yet validated/persisted.

    Not deliverable to a student: it has not passed
    netra_api.learning.quiz.validator.validate_question_draft and has no
    question_id/version yet.
    """

    concept_id: str
    kind: QuestionKind
    prompt: str = Field(min_length=1, max_length=2000)
    options: list[QuestionOption] = Field(default_factory=list)
    answer_key: AnswerKey


class ApprovedQuestion(BaseModel):
    """A validated, persisted question ready to be delivered to a student.

    Only netra_api.learning.quiz.repository.PendingQuestionRepository
    may create the persisted record this represents (CLAUDE.md "Persist
    a pending question before delivering it to the student").
    """

    question_id: str
    question_version: int = Field(ge=1)
    concept_id: str
    kind: QuestionKind
    prompt: str
    options: list[QuestionOption] = Field(default_factory=list)
    answer_key: AnswerKey
    created_at: datetime


class StudentFacingQuestion(BaseModel):
    """What is safe to send to the client: no answer_key, ever."""

    question_id: str
    question_version: int
    kind: QuestionKind
    prompt: str
    options: list[QuestionOption] = Field(default_factory=list)

    @classmethod
    def from_approved(cls, question: ApprovedQuestion) -> "StudentFacingQuestion":
        return cls(
            question_id=question.question_id,
            question_version=question.question_version,
            kind=question.kind,
            prompt=question.prompt,
            options=question.options,
        )
