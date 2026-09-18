"""Strict parsing of Coordinator model decisions.

The model's output is untrusted until validated. A decision is either inert
tool-call requests (ModelDecision.tool_calls) or one JSON object in raw_text
matching CoordinatorDecisionPayload. Anything else — unknown fields, prose
around the JSON, an action inconsistent with the tool calls — is rejected and
costs the attempted model decision it already consumed; the next decision is
told which check failed.

There is intentionally no "reasoning" field. Requirements and assessments are
short, bounded, inspectable claims that the application verifies against
validated evidence (coordinator/evidence_check.py); they are not chain of
thought and are not stored as such.
"""

from __future__ import annotations

import json
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from netra_api.coordinator.handoff import ExplanationLevel, HandoffMode
from netra_api.coordinator.providers.gemini import ModelDecision, ToolCallRequest

DecisionAction = Literal["call_tools", "answer", "delegate_to_tutor", "clarify", "state_gap"]
AssessmentStatus = Literal["supported", "missing", "unreadable"]

MAX_TOOL_CALLS_PER_DECISION = 6


class InvalidDecisionError(Exception):
    """The model output failed validation. ``check`` is a safe, specific code."""

    def __init__(self, check: str) -> None:
        self.check = check
        super().__init__(check)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Requirement(_Strict):
    """Something the answer must be able to show from evidence, e.g. graph axes."""

    requirement_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_\-]{0,47}$")
    description: str = Field(min_length=1, max_length=300)


class Assessment(_Strict):
    requirement_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_\-]{0,47}$")
    status: AssessmentStatus
    evidence_id: Optional[str] = Field(default=None, max_length=200)
    observation_label: Optional[str] = Field(default=None, max_length=200)
    gap: Optional[str] = Field(default=None, max_length=300)


class TutorRequest(_Strict):
    mode: HandoffMode
    learning_goal: str = Field(min_length=1, max_length=2000)
    explanation_level: ExplanationLevel = "standard"
    target_concept_ids: tuple[str, ...] = Field(default=(), max_length=12)
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=12)


class CoordinatorDecisionPayload(_Strict):
    action: DecisionAction
    requirements: tuple[Requirement, ...] = Field(default=(), max_length=8)
    assessments: tuple[Assessment, ...] = Field(default=(), max_length=8)
    text: Optional[str] = Field(default=None, min_length=1, max_length=4000)
    cited_evidence_ids: tuple[str, ...] = Field(default=(), max_length=12)
    tutor: Optional[TutorRequest] = None

    @model_validator(mode="after")
    def _action_fields(self) -> "CoordinatorDecisionPayload":
        if self.action in ("answer", "clarify", "state_gap") and not self.text:
            raise ValueError("text required")
        if self.action == "delegate_to_tutor" and self.tutor is None:
            raise ValueError("tutor request required")
        if self.action != "delegate_to_tutor" and self.tutor is not None:
            raise ValueError("tutor request only allowed when delegating")
        return self


class ParsedDecision(_Strict):
    payload: CoordinatorDecisionPayload
    tool_calls: tuple[ToolCallRequest, ...] = ()


def parse_decision(decision: ModelDecision) -> ParsedDecision:
    raw = (decision.raw_text or "").strip()
    calls = tuple(decision.tool_calls)

    if len(calls) > MAX_TOOL_CALLS_PER_DECISION:
        raise InvalidDecisionError("too_many_tool_calls")

    if not raw:
        if not calls:
            raise InvalidDecisionError("empty_decision")
        return ParsedDecision(payload=CoordinatorDecisionPayload(action="call_tools"), tool_calls=calls)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InvalidDecisionError("decision_not_json") from exc
    if not isinstance(data, dict):
        raise InvalidDecisionError("decision_not_object")
    try:
        payload = CoordinatorDecisionPayload.model_validate(data)
    except ValidationError as exc:
        raise InvalidDecisionError("decision_schema_invalid") from exc

    if payload.action == "call_tools" and not calls:
        raise InvalidDecisionError("call_tools_without_tool_calls")
    if payload.action != "call_tools" and calls:
        raise InvalidDecisionError("tool_calls_with_final_action")
    return ParsedDecision(payload=payload, tool_calls=calls)
