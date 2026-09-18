"""Factual learning-activity records (D3) — PROPOSED, not an approved schema.

current-scope.md: the Learning service "validates and commits proposals,
including delivered study activity, answers, student-stated reasoning,
feedback and assistance". AssessmentAttempt can only represent a graded
answer; everything else needs the record defined here. This module is the
concrete M4 proposal for docs/team/handoffs/M4.md D3 / INT-07, written as
code so M2 can review a migration against an exact shape and M1/M5 can
review what they must supply. It is not wired into the Tutor loop or
LearningService.propose_event (which still fails closed for non-answer
events) until M1/M2/M5 approve it; approval may change it.

Design rules, each tied to an authority:

- **Facts only.** Kinds describe what happened — an explanation or hint
  was generated, feedback was generated for an attempt, the student
  stated their reasoning, a transcript was corrected. There is no status,
  mastery, misconception or "understanding" field, and no
  "Studied — understanding not tested" value: that phrase is wording the
  client renders from the absence of attempts (current-scope.md).
- **Generated is not delivered.** An ActivityRecord says content was
  generated and committed. Whether it was sent, played or acknowledged is
  a separate DeliveryFact appended by the path that observed it (M1
  transport for sent; M5's playback acknowledgement through M1 for played
  and acknowledged). Nothing here infers a later stage from an earlier
  one (current-scope.md: "Do not claim the student heard text merely
  because it was generated or sent").
- **Append-only and replay-safe.** activity_id is derived from the
  originating request_id, kind and ordinal, so a retransmitted turn
  reproduces the same id and the repository returns the existing record.
- **Assistance stays per attempt.** A hint references the question it
  helped with; the eventual attempt's hints_used already records how many.
  Feedback and stated reasoning reference the attempt they belong to.
- **Corrections keep the original.** A transcript correction stores both
  the original and corrected text; neither overwrites an attempt.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_ACTIVITY_TEXT_CHARS = 8000
"""Mirrors the contract maximum for public segment and answer text."""


class ActivityKind(str, Enum):
    EXPLANATION_GENERATED = "explanation_generated"
    HINT_GENERATED = "hint_generated"
    FEEDBACK_GENERATED = "feedback_generated"
    STUDENT_REASONING_STATED = "student_reasoning_stated"
    TRANSCRIPT_CORRECTED = "transcript_corrected"


class DeliveryStage(str, Enum):
    """Stages after generation, each recorded only by the path that saw it."""

    SENT = "sent"
    PLAYED = "played"
    ACKNOWLEDGED = "acknowledged"


_ACTIVITY_ID_NAMESPACE = UUID("7c3e9d42-1b6a-4f0e-8a5d-2e4b6c8d0f13")
"""Fixed namespace for replay-safe activity ids; must never change once
records exist (same reasoning as assessment.service._ATTEMPT_ID_NAMESPACE)."""


def derive_activity_id(request_id: UUID, kind: ActivityKind, ordinal: int) -> UUID:
    """Deterministic id: the same turn's same record always gets the same id."""

    if ordinal < 0:
        raise ValueError("ordinal must be >= 0")
    return uuid5(_ACTIVITY_ID_NAMESPACE, f"{request_id}:{kind.value}:{ordinal}")


class ActivityProposal(BaseModel):
    """What a producer submits to ActivityHistoryService.record.

    account/session identity is never taken from here: the service uses the
    AuthContext. ordinal distinguishes several records of one kind in one
    turn (normally 0).
    """

    model_config = ConfigDict(extra="forbid")

    kind: ActivityKind
    lesson_id: UUID
    concept_ids: list[str] = Field(default_factory=list, max_length=12)
    text: str = Field(min_length=1, max_length=MAX_ACTIVITY_TEXT_CHARS)
    """Verbatim: generated content, the student's stated reasoning, or the
    corrected transcript. Private learning history; never exported to
    traces or evaluation fixtures."""
    evidence_ids: list[str] = Field(default_factory=list, max_length=12)
    question_id: Optional[str] = None
    question_version: Optional[int] = Field(default=None, ge=1)
    attempt_id: Optional[UUID] = None
    original_text: Optional[str] = Field(default=None, max_length=MAX_ACTIVITY_TEXT_CHARS)
    """TRANSCRIPT_CORRECTED only: the transcript as first recognised."""
    ordinal: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _kind_requirements(self) -> "ActivityProposal":
        kind = self.kind
        has_question = self.question_id is not None and self.question_version is not None
        if (self.question_id is None) != (self.question_version is None):
            raise ValueError("question_id and question_version are supplied together")
        if kind in (ActivityKind.EXPLANATION_GENERATED, ActivityKind.HINT_GENERATED, ActivityKind.FEEDBACK_GENERATED):
            if not self.evidence_ids:
                raise ValueError(f"{kind.value} must cite the evidence it was generated from")
        if kind is ActivityKind.HINT_GENERATED and not has_question:
            raise ValueError("a hint must reference the pending question it assisted with")
        if kind is ActivityKind.HINT_GENERATED and self.attempt_id is not None:
            raise ValueError("a hint precedes the attempt; it references the question, not an attempt")
        if kind in (ActivityKind.FEEDBACK_GENERATED, ActivityKind.STUDENT_REASONING_STATED):
            if self.attempt_id is None:
                raise ValueError(f"{kind.value} must reference the attempt it belongs to")
        if kind is ActivityKind.TRANSCRIPT_CORRECTED:
            if not self.original_text:
                raise ValueError("a transcript correction keeps the original transcript")
            if self.original_text == self.text:
                raise ValueError("a transcript correction must change the transcript")
        elif self.original_text is not None:
            raise ValueError("original_text is only meaningful for transcript corrections")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("evidence_ids must be unique")
        return self


class ActivityRecord(BaseModel):
    """One committed, append-only factual activity record."""

    model_config = ConfigDict(frozen=True)

    activity_id: UUID
    account_id: UUID
    session_id: UUID
    request_id: UUID
    lesson_id: UUID
    kind: ActivityKind
    concept_ids: tuple[str, ...]
    text: str
    evidence_ids: tuple[str, ...]
    question_id: Optional[str] = None
    question_version: Optional[int] = None
    attempt_id: Optional[UUID] = None
    original_text: Optional[str] = None
    recorded_at: datetime


class DeliveryFact(BaseModel):
    """That one generated activity reached a later delivery stage.

    PROPOSED shape for M1/M5: appended by the transport (sent) and by
    playback acknowledgement handling (played/acknowledged), keyed by
    (activity_id, stage) so a repeated acknowledgement is idempotent. The
    Learning service never writes one on a producer's behalf.
    """

    model_config = ConfigDict(frozen=True)

    activity_id: UUID
    stage: DeliveryStage
    generation_id: Optional[str] = None
    """The client-visible generation the activity was delivered in, when
    known, so a cancelled/superseded generation is distinguishable."""
    observed_at: datetime
