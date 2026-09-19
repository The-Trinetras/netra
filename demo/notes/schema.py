"""The records this slice passes between steps.

Ported from the earlier project's coordinator/decisions.py (Requirement,
Assessment, and the rule that an answer or a stated gap is one strict object),
reshaped into the kit's two-record pattern: the drafter writes an AnswerDraft,
the gate writes a Verdict. Nothing crosses a step boundary as prose.

Differences from the original, on purpose:
  * no tool calls and no Tutor delegation here (this slice retrieves once, up front);
  * assessment status is "supported" or "missing" only - "unreadable" was for
    figures and scanned pages, which this slice does not have;
  * no reasoning field. Requirements and assessments are short, checkable claims,
    not chain of thought.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

ID = r"^[a-z0-9][a-z0-9_\-]{0,47}$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Requirement(_Strict):
    """Something the answer must be able to show from the notes."""

    requirement_id: str = Field(pattern=ID)
    description: str = Field(min_length=1, max_length=300)


class Assessment(_Strict):
    """The drafter's claim about one requirement. Code verifies it; see ledger.py."""

    requirement_id: str = Field(pattern=ID)
    status: Literal["supported", "missing"]
    evidence_id: Optional[str] = Field(default=None, max_length=200)
    gap: Optional[str] = Field(default=None, max_length=300)


class AnswerDraft(_Strict):
    """What the drafter produces: an answer that cites evidence, or a stated gap."""

    action: Literal["answer", "state_gap"]
    requirements: list[Requirement] = Field(min_length=1, max_length=8)
    assessments: list[Assessment] = Field(default_factory=list, max_length=8)
    text: str = Field(min_length=1, max_length=4000)
    cited_evidence_ids: list[str] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def _gap_cites_nothing(self) -> "AnswerDraft":
        if self.action == "state_gap" and self.cited_evidence_ids:
            raise ValueError("a stated gap cites nothing")
        return self


class Objection(BaseModel):
    """One defect in THIS draft. Never generic advice."""

    requirement_id: Optional[str] = Field(
        default=None, description="The requirement at fault, if the defect belongs to one")
    problem: str = Field(description="The specific defect, quoting the offending text")


class Verdict(BaseModel):
    """What the gate produces. The only record that moves the run."""

    status: Literal["PASS", "BLOCK"]
    objections: list[Objection] = Field(default_factory=list)
