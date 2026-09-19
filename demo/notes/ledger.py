"""Evidence ledger: validated evidence, declared requirements, specific gaps.

Ported from the earlier project's coordinator/evidence_check.py. The idea it
keeps: "supported" is not the model's say-so. A model may CLAIM that a
requirement is supported, but code verifies the claim:

  * only evidence that was actually retrieved enters the ledger; a model that
    cites any other id cites nothing;
  * a supported claim whose cited evidence is not in the ledger becomes a gap;
  * an answer is accepted only if every declared requirement is supported and
    every cited id is validated evidence.

What code cannot prove is that the cited TEXT really says what the answer claims.
That is semantic, so it is the gate model's job (see flow.py), and it is said out
loud here rather than hidden: a claim that passes this ledger is "cites real
evidence", not "is true".

Differences from the original: no structured observations (those belong to figures
and tables, not plain notes), and the repetition rule lives in the flow's stop
rules, not here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Literal, Optional

from .schema import AnswerDraft, Assessment, Requirement

RequirementStatus = Literal["open", "supported", "missing"]

GAP_NOT_VALIDATED = "cited_evidence_not_validated"
GAP_REPORTED_MISSING = "reported_missing"


@dataclass(frozen=True)
class Evidence:
    """A retrieved passage the drafter may cite."""

    evidence_id: str
    doc: str
    ordinal: int
    text: str

    @property
    def locator(self) -> str:
        return f"{self.doc}#{self.ordinal}"

    @classmethod
    def from_chunk(cls, chunk) -> "Evidence":
        return cls(chunk.chunk_id, chunk.doc, int(chunk.ordinal), chunk.text)


@dataclass
class RequirementState:
    requirement: Requirement
    status: RequirementStatus = "open"
    evidence_id: Optional[str] = None
    gap_code: Optional[str] = None


@dataclass(frozen=True)
class GapRecord:
    requirement_id: str
    status: RequirementStatus
    gap_code: str
    evidence_id: Optional[str]


@dataclass
class EvidenceLedger:
    evidence: dict[str, Evidence] = field(default_factory=dict)
    requirements: dict[str, RequirementState] = field(default_factory=dict)
    undeclared: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------ inputs

    def add_evidence(self, items: Iterable[Evidence]) -> list[str]:
        new_ids = []
        for item in items:
            if item.evidence_id not in self.evidence:
                new_ids.append(item.evidence_id)
            self.evidence[item.evidence_id] = item
        return new_ids

    def add_requirements(self, requirements: Iterable[Requirement]) -> list[str]:
        added = []
        for requirement in requirements:
            if requirement.requirement_id not in self.requirements:
                self.requirements[requirement.requirement_id] = RequirementState(requirement)
                added.append(requirement.requirement_id)
        return added

    def apply_assessments(self, assessments: Iterable[Assessment]) -> list[GapRecord]:
        """Verify the drafter's claims and return the gaps they leave or reveal."""
        gaps: list[GapRecord] = []
        for assessment in assessments:
            state = self.requirements.get(assessment.requirement_id)
            if state is None:
                # The original ignores these silently. Here it is a recorded problem:
                # an assessment of something nobody declared is a sign of a confused draft.
                self.undeclared.append(assessment.requirement_id)
                continue
            if assessment.status == "supported":
                status, code = self._verify_support(assessment)
            else:
                status, code = "missing", GAP_REPORTED_MISSING
            state.status = status
            state.evidence_id = assessment.evidence_id
            state.gap_code = code
            if status != "supported":
                gaps.append(GapRecord(assessment.requirement_id, status, code or "unresolved",
                                      assessment.evidence_id))
        return gaps

    def _verify_support(self, assessment: Assessment) -> tuple[RequirementStatus, Optional[str]]:
        if assessment.evidence_id is None or assessment.evidence_id not in self.evidence:
            return "missing", GAP_NOT_VALIDATED
        return "supported", None

    # ----------------------------------------------------------------- queries

    def open_requirements(self) -> list[RequirementState]:
        return [s for s in self.requirements.values() if s.status != "supported"]

    def supported_requirements(self) -> list[RequirementState]:
        return [s for s in self.requirements.values() if s.status == "supported"]

    def unvalidated(self, evidence_ids: Iterable[str]) -> list[str]:
        return [e for e in evidence_ids if e not in self.evidence]

    # ------------------------------------------------------------ the one check

    def check_draft(self, draft: AnswerDraft) -> list[str]:
        """Everything code can verify about a draft. Empty means the draft passes
        the ledger; it does NOT mean the answer is correct (see module docstring).

        Each problem is a short safe code, optionally with ':' and detail. The flow
        turns them into objections the drafter can act on.
        """
        self.add_requirements(draft.requirements)
        self.apply_assessments(draft.assessments)
        problems: list[str] = []

        for requirement_id in self.undeclared:
            problems.append(f"assessment_for_undeclared_requirement:{requirement_id}")

        if draft.action == "state_gap":
            if not self.open_requirements():
                problems.append("gap_stated_but_every_requirement_supported")
            return problems

        if not draft.cited_evidence_ids:
            problems.append("no_validated_evidence_cited")
        unvalidated = self.unvalidated(draft.cited_evidence_ids)
        if unvalidated:
            problems.append("cited_evidence_not_validated:" + ",".join(unvalidated))
        for state in self.open_requirements():
            detail = state.gap_code or "not_assessed"
            problems.append(f"requirement_unresolved:{state.requirement.requirement_id}:{detail}")
        return problems
