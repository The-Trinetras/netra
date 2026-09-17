"""Evidence ledger: validated evidence, declared requirements and specific gaps.

The Coordinator's differentiator is that an insufficient tool result changes
the next action. This ledger is where "insufficient" becomes a checked,
recorded fact rather than a model's say-so:

- Only evidence returned through the tool gateway (authorized, bounded,
  version-pinned) enters the ledger. A model citing any other id cites
  nothing.
- A model may claim a requirement is supported, but the application verifies
  the claim: the cited evidence must be in the ledger, and when the claim
  names a structured observation (e.g. "x-axis label"), that observation must
  exist and be OBSERVED or ESTIMATED. UNREADABLE stays a gap ("unreadable"),
  GENERATED is not a source observation ("not_observed_in_source"), and an
  ESTIMATED value is accepted but flagged approximate.
- Semantic support of free text cannot be proven mechanically; a supported
  claim citing plain text evidence is accepted as the model's assessment and
  recorded as such for source review. That limitation is documented, not hidden.

Repetition: an action is identified by tool name and canonical arguments.
Re-running an action that already ran in this turn while requirements are
still unresolved cannot produce different authorized evidence, so it is
blocked. This is a no-progress rule, not a numeric revision cap (no cap is
approved).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

from netra_api.coordinator.decisions import Assessment, Requirement
from netra_api.coordinator.tool_registry import ToolEvidence
from netra_api.multimedia.evidence import ObservationSource

RequirementStatus = Literal["open", "supported", "missing", "unreadable"]


@dataclass
class RequirementState:
    requirement: Requirement
    status: RequirementStatus = "open"
    evidence_id: Optional[str] = None
    gap_code: Optional[str] = None
    approximate: bool = False


@dataclass(frozen=True)
class GapRecord:
    requirement_id: str
    status: RequirementStatus
    gap_code: str
    evidence_id: Optional[str]


@dataclass
class EvidenceLedger:
    evidence: dict[str, ToolEvidence] = field(default_factory=dict)
    requirements: dict[str, RequirementState] = field(default_factory=dict)
    executed_actions: set[str] = field(default_factory=set)
    rejected_evidence_count: int = 0

    def add_requirements(self, requirements: tuple[Requirement, ...]) -> list[str]:
        added = []
        for requirement in requirements:
            if requirement.requirement_id not in self.requirements:
                self.requirements[requirement.requirement_id] = RequirementState(requirement)
                added.append(requirement.requirement_id)
        return added

    def add_evidence(self, items: tuple[ToolEvidence, ...]) -> list[str]:
        new_ids = []
        for item in items:
            if item.evidence_id not in self.evidence:
                new_ids.append(item.evidence_id)
            self.evidence[item.evidence_id] = item
        return new_ids

    def apply_assessments(self, assessments: tuple[Assessment, ...]) -> list[GapRecord]:
        """Verify assessment claims and return the gaps they leave or reveal."""

        gaps: list[GapRecord] = []
        for assessment in assessments:
            state = self.requirements.get(assessment.requirement_id)
            if state is None:
                continue
            if assessment.status == "supported":
                status, code, approximate = self._verify_support(assessment)
            elif assessment.status == "unreadable":
                status, code, approximate = "unreadable", "reported_unreadable", False
            else:
                status, code, approximate = "missing", "reported_missing", False

            state.status = status
            state.evidence_id = assessment.evidence_id
            state.gap_code = code
            state.approximate = approximate
            if status != "supported":
                gaps.append(GapRecord(assessment.requirement_id, status, code or "unresolved", assessment.evidence_id))
        return gaps

    def _verify_support(self, assessment: Assessment) -> tuple[RequirementStatus, Optional[str], bool]:
        evidence = self.evidence.get(assessment.evidence_id or "")
        if evidence is None:
            return "missing", "cited_evidence_not_validated", False
        if assessment.observation_label is None:
            return "supported", None, False

        wanted = assessment.observation_label.strip().casefold()
        matches = [obs for obs in evidence.observations if obs.label.strip().casefold() == wanted]
        if not matches:
            return "missing", "observation_not_in_evidence", False
        sources = {obs.source for obs in matches}
        if ObservationSource.OBSERVED in sources:
            return "supported", None, False
        if ObservationSource.ESTIMATED in sources:
            return "supported", None, True
        if ObservationSource.UNREADABLE in sources:
            return "unreadable", "unreadable_in_source", False
        return "missing", "not_observed_in_source", False

    def open_requirements(self) -> list[RequirementState]:
        return [state for state in self.requirements.values() if state.status != "supported"]

    def supported_requirements(self) -> list[RequirementState]:
        return [state for state in self.requirements.values() if state.status == "supported"]

    def is_unproductive_repeat(self, action_key: str) -> bool:
        return action_key in self.executed_actions and bool(self.open_requirements())

    def unvalidated(self, evidence_ids: tuple[str, ...]) -> list[str]:
        return [evidence_id for evidence_id in evidence_ids if evidence_id not in self.evidence]
