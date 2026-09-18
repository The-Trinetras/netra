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
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from netra_api.content.retrieval.evidence import EvidenceTrust


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


class QuestionEvidenceRef(BaseModel):
    """Which authorized evidence an approved question was written from.

    Stored with the question so an answer can be traced back to the exact
    source version the question was grounded in, and so a later check (on
    deletion or a version change, for example) has canonical identities to
    re-validate. Only identities are kept; evidence text stays in its own
    authoritative store. Built only from resolver-returned Evidence by
    netra_api.learning.quiz.validator.evidence_refs_for, never from
    model output.
    """

    model_config = ConfigDict(frozen=True)

    evidence_id: str
    source_version_id: UUID
    trust: EvidenceTrust


class QuestionDraft(BaseModel):
    """A quiz question the Tutor has proposed but not yet validated/persisted.

    Not deliverable to a student: it has not passed
    netra_api.learning.quiz.validator.validate_question_for_approval and
    has no question_id/version yet.
    """

    concept_id: str
    kind: QuestionKind
    prompt: str = Field(min_length=1, max_length=2000)
    options: list[QuestionOption] = Field(default_factory=list)
    answer_key: AnswerKey
    evidence_ids: list[str] = Field(default_factory=list, max_length=12)
    """The evidence ids the draft claims it was written from.

    Model-proposed, therefore untrusted: a citation is only a claim until
    netra_api.learning.quiz.validator.bind_draft_to_evidence binds each
    id to evidence this turn actually resolved. Empty is representable so
    an uncited draft reaches validation and is refused there with a
    recorded reason, rather than failing as an opaque parse error."""


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
    evidence_refs: list[QuestionEvidenceRef] = Field(default_factory=list, max_length=12)
    """The authorized evidence this question was bound to at approval.

    Defaults to empty only so that a record written before binding existed
    stays readable; the Tutor's approval path always populates it. Like
    answer_key, server-side only: StudentFacingQuestion does not carry it.
    PROPOSED persistence shape for M2's pending_questions migration (see
    docs/team/handoffs/M4.md, D2)."""


class StudentFacingQuestion(BaseModel):
    """What is safe to send to the client: no answer_key, ever.

    This is the quiz.question wire payload (session.snapshot decision B,
    2026-09-12): every field here is exactly what
    shared/contracts/protocol/v1/server_to_client.schema.json's
    QuizQuestion $def carries. hints_used is not part of ApprovedQuestion
    itself — it comes from the session's PendingQuestionRef, which tracks
    per-attempt hint usage separately from the immutable question record.
    """

    question_id: str
    question_version: int
    kind: QuestionKind
    prompt: str
    options: list[QuestionOption] = Field(default_factory=list)
    hints_used: int = Field(default=0, ge=0)

    @classmethod
    def from_approved(cls, question: ApprovedQuestion, hints_used: int = 0) -> "StudentFacingQuestion":
        return cls(
            question_id=question.question_id,
            question_version=question.question_version,
            kind=question.kind,
            prompt=question.prompt,
            options=question.options,
            hints_used=hints_used,
        )
