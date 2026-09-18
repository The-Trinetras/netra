"""Quiz generation — a bounded Tutor capability, not a separate agent.

CLAUDE.md "Tutor": the Tutor owns "proposing quizzes." Generation itself
runs through the Tutor's provider adapter (see
netra_api.learning.tutor.providers.groq); this module only fixes the
typed request/response shape so tutor/agent.py never depends on a raw
provider response. A generated QuestionDraft is not yet deliverable —
callers must pass it through
netra_api.learning.quiz.validator.validate_question_draft, and then
persist it via netra_api.learning.quiz.repository.PendingQuestionRepository,
before it may reach a student (CLAUDE.md "Persist a pending question
before delivering it to the student").
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, Field

from netra_api.coordinator.handoff import EvidenceRef, ExplanationLevel
from netra_api.learning.quiz.models import QuestionDraft


class QuizGenerationRequest(BaseModel):
    """What the Tutor needs to propose one question for one concept."""

    concept_id: str
    explanation_level: ExplanationLevel
    evidence_refs: list[EvidenceRef] = Field(min_length=1, max_length=12)
    """Grounds the generated question in already-authorized evidence
    (CLAUDE.md "Evidence rules") rather than the model inventing content.
    An implementation reports which of these it used in
    QuestionDraft.evidence_ids; the Tutor binds those ids to resolved
    evidence and refuses a draft that cites anything else."""


class QuizGenerator(Protocol):
    """Tutor-side bounded step that proposes a QuestionDraft.

    Produces an unvalidated draft; the draft's answer_key must never be
    surfaced to the client directly from here — see
    netra_api.learning.quiz.models.StudentFacingQuestion.
    """

    async def generate(self, request: QuizGenerationRequest) -> QuestionDraft:
        ...
