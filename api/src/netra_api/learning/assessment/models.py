"""Assessment-attempt and learning-status domain models.

CLAUDE.md "Tutor learning rules": "Assessment history is append-oriented
evidence. Do not overwrite history with a single 'mastery score.'"
PostgreSQL is the authoritative store for these records (CLAUDE.md "Data
authority": "assessment attempts/current learning status -> Learning
service in PostgreSQL"). There is deliberately no mutable score field
anywhere in this module — see
netra_api.learning.assessment.service.derive_status_from_history, which
recomputes a LearningStatus label from history instead of storing one
directly.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from netra_api.coordinator.handoff import LearningEventType

EvaluatedBy = Literal["tutor", "grader"]
"""Which bounded actor produced an AssessmentAttempt.outcome: the Tutor
(rubric-graded free text — CLAUDE.md "Tutor": "evaluating an answer
against an approved question/rubric") or the deterministic grader
(objective question kinds — see netra_api.learning.assessment.grader)."""


class LearningStatus(str, Enum):
    """Mirrors AssessmentStatus in shared/contracts/agent/v1/*.schema.json.

    CLAUDE.md "Tutor learning rules": "Do not claim probabilistic
    mastery." These four labels are the entire vocabulary; there is no
    numeric score anywhere in this domain.
    """

    NOT_ASSESSED = "not_assessed"
    NEEDS_REVIEW = "needs_review"
    DEVELOPING = "developing"
    DEMONSTRATED_RECENTLY = "demonstrated_recently"


class AttemptOutcome(str, Enum):
    """Objective outcome of one graded attempt.

    Not itself a mastery label — LearningStatus is derived from a
    sequence of these (see
    netra_api.learning.assessment.service.derive_status_from_history).
    """

    CORRECT = "correct"
    INCORRECT = "incorrect"
    PARTIAL = "partial"


class AnswerSubmission(BaseModel):
    """The text one attempt is graded against.

    original_transcript is preserved for audit when speech-to-text
    produced it; corrected_text, when present, is what the student (or
    a correction flow) changed it to before finalizing. Grading always
    evaluates final_text — a correction is not itself a wrong answer
    (CLAUDE.md "Tutor learning rules": "Corrected speech/transcript
    should not automatically count as failure").
    """

    original_transcript: Optional[str] = Field(default=None, max_length=8000)
    corrected_text: Optional[str] = Field(default=None, max_length=8000)
    final_text: str = Field(min_length=1, max_length=8000)

    @property
    def was_corrected(self) -> bool:
        return self.corrected_text is not None


class AssessmentAttempt(BaseModel):
    """One append-only record of a student's attempt at a concept check.

    Attempts are never updated or deleted — see
    netra_api.learning.assessment.repository.AssessmentHistoryRepository,
    which has no update/delete method by design.
    """

    attempt_id: UUID
    account_id: UUID
    concept_id: str
    question_id: str
    question_version: int = Field(ge=1)
    answer: AnswerSubmission
    outcome: AttemptOutcome
    hints_used: int = Field(default=0, ge=0)
    evaluated_by: EvaluatedBy
    created_at: datetime


class CurrentLearningStatus(BaseModel):
    """Result of deriving a LearningStatus from AssessmentAttempt history.

    Recomputed on demand (see
    netra_api.learning.assessment.service.derive_status_from_history);
    never itself persisted as authoritative state.
    """

    concept_id: str
    status: LearningStatus
    based_on_attempts: int = Field(ge=0)
    last_assessed_at: Optional[datetime] = None


class LearningEventProposal(BaseModel):
    """What the Tutor submits to LearningService to propose a learning event.

    Richer than netra_api.coordinator.handoff.ProposedLearningEvent (the
    thin summary reported back to the Coordinator in a handoff result)
    because committing an authoritative AssessmentAttempt needs the
    actual answer/outcome detail. The Tutor only ever proposes this —
    LearningService.propose_event validates and commits it (CLAUDE.md
    "Tutor learning rules": "Tutor output may PROPOSE a learning event.
    The Learning service validates and commits authoritative assessment
    changes."). There is deliberately no field here through which a
    caller could set a LearningStatus/mastery label directly (CLAUDE.md
    "Tutor must not ... directly set mastery") — status is always
    derived from committed attempts afterward, never assigned.
    """

    account_id: UUID
    concept_id: str
    event_type: LearningEventType
    question_id: Optional[str] = None
    question_version: Optional[int] = Field(default=None, ge=1)
    answer: Optional[AnswerSubmission] = None
    outcome: Optional[AttemptOutcome] = None
    hints_used: int = Field(default=0, ge=0)
