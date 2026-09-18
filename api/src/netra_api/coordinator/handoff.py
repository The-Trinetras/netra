"""Pydantic mirror of shared/contracts/agent/v1/*.schema.json.

This module must stay field-for-field identical to the JSON Schema
contracts; it does not redesign or extend them (CLAUDE.md "Protocol
rules": "Do not silently add protocol fields inside only one
application"). Any contract change goes through shared/contracts/ first
(a new handoff_version), then here.

Coordinator<->Tutor communication uses only this typed handoff. Never
implement free-form agent-to-agent chat, and never pass the
Coordinator's private chain-of-thought to the Tutor.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _StrictHandoffModel(BaseModel):
    """Base for every handoff model: unknown fields are a validation error.

    Both agent schemas set "additionalProperties": false. Pydantic's
    default is to ignore unknown fields, so without this a handoff
    carrying an extra field would be accepted here and rejected by the
    schema, and the two sides would drift apart quietly.

    It also closes a leak path. The richer internal
    netra_api.learning.assessment.models.LearningEventProposal carries a
    student's answer text and outcome; forbidding extras means an
    attempt to build a wire model from one fails loudly instead of
    silently dropping the sensitive fields and appearing to work.
    """

    model_config = ConfigDict(extra="forbid")


HandoffMode = Literal["explain", "continue_lesson", "check_understanding", "evaluate_answer"]
ExplanationLevel = Literal["brief", "standard", "detailed"]
AssessmentStatus = Literal["not_assessed", "needs_review", "developing", "demonstrated_recently"]
TutorResultStatus = Literal["completed", "awaiting_student_answer", "needs_more_evidence", "failed"]
PublicSegmentKind = Literal["explanation", "hint", "question", "correction"]
LearningEventType = Literal["concept_exposed", "hint_used", "answer_evaluated", "review_requested"]


class EvidenceRef(_StrictHandoffModel):
    evidence_id: str
    source_version_id: str
    """Canonical UUID string of the immutable source version (schema
    ``format: uuid``). Any UUID spelling is normalized to the lowercase
    hyphenated form; a non-UUID value is refused at construction, so a
    handoff can never carry a version the Tutor cannot verify. evidence_id
    is deliberately NOT parsed: multimedia evidence ids are not UUIDs."""
    evidence_version: int = Field(ge=1)

    @field_validator("source_version_id", mode="before")
    @classmethod
    def _canonical_source_version(cls, value: object) -> str:
        try:
            return str(value if isinstance(value, UUID) else UUID(str(value).strip()))
        except ValueError as exc:
            raise ValueError("source_version_id must be the source version's UUID") from exc


class DialogueTurn(_StrictHandoffModel):
    role: Literal["student", "tutor"]
    content: str = Field(max_length=4000)


class AssessmentSummary(_StrictHandoffModel):
    concept_id: str
    status: AssessmentStatus
    last_assessed_at: Optional[datetime] = None


class PendingQuestion(_StrictHandoffModel):
    question_id: str
    question_version: int = Field(ge=1)
    hints_used: int = Field(ge=0)


class CoordinatorToTutorHandoff(_StrictHandoffModel):
    """Mirrors shared/contracts/agent/v1/coordinator_to_tutor.schema.json."""

    handoff_version: Literal["1.0"] = "1.0"
    handoff_id: UUID
    request_id: UUID
    session_id: UUID
    lesson_id: UUID
    mode: HandoffMode
    learning_goal: str = Field(min_length=1, max_length=2000)
    original_utterance: str = Field(min_length=1, max_length=8000)
    target_concept_ids: list[str] = Field(max_length=12)
    explanation_level: ExplanationLevel
    evidence_refs: list[EvidenceRef] = Field(min_length=1, max_length=12)
    recent_dialogue: list[DialogueTurn] = Field(max_length=8)
    assessment_summaries: list[AssessmentSummary] = Field(max_length=12)
    pending_question: Optional[PendingQuestion] = None
    deadline_at: datetime

    @field_validator("target_concept_ids")
    @classmethod
    def _unique_target_concept_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("target_concept_ids must be unique")
        return value


class PublicSegment(_StrictHandoffModel):
    kind: PublicSegmentKind
    text: str = Field(max_length=8000)


class ProposedLearningEvent(_StrictHandoffModel):
    event_type: LearningEventType
    concept_id: str
    attempt_id: Optional[str] = None


class TutorToCoordinatorResult(_StrictHandoffModel):
    """Mirrors shared/contracts/agent/v1/tutor_to_coordinator.schema.json."""

    handoff_version: Literal["1.0"] = "1.0"
    handoff_id: UUID
    status: TutorResultStatus
    public_segments: list[PublicSegment] = Field(max_length=12)
    pending_question_id: Optional[str] = None
    evidence_ids: list[str]
    proposed_learning_events: list[ProposedLearningEvent] = Field(max_length=12)
    decision_summary: Optional[str] = Field(default=None, max_length=1000)

    @field_validator("evidence_ids")
    @classmethod
    def _unique_evidence_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("evidence_ids must be unique")
        return value
