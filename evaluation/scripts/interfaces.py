"""Learning evaluation interfaces.

CLAUDE.md "Architecture: only two agents" lists "evaluation" as a
bounded workflow, not a third agent, and "Code ownership" assigns
Member 4 "evaluation of teaching/learning behavior." This module fixes
the typed shape a Tutor-behavior evaluation run produces, so
evaluation/scripts/ tooling, evaluation/rubrics/ criteria, and
evaluation/locked/ frozen cases share one contract instead of ad hoc
dicts. No evaluation runner is implemented here — only the interfaces
and models the runner will produce/consume.

Evaluation is read-only with respect to the rest of the system: it
grades an already-produced
netra_api.coordinator.handoff.TutorToCoordinatorResult against a
rubric and never itself proposes or commits a learning event (that
stays exclusively the Learning service's job — see
netra_api.learning.assessment.service.LearningService).
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Protocol

from pydantic import BaseModel, Field

from netra_api.coordinator.handoff import CoordinatorToTutorHandoff, TutorToCoordinatorResult


class RubricCriterion(BaseModel):
    """One gradable dimension of a Tutor response, e.g. "cites evidence"
    or "matches the handoff's explanation_level"."""

    criterion_id: str
    description: str
    weight: float = Field(default=1.0, gt=0)


class Rubric(BaseModel):
    """A named, versioned set of criteria.

    Lives as data under evaluation/rubrics/; this model only fixes its
    shape.
    """

    rubric_id: str
    rubric_version: int = Field(ge=1)
    criteria: list[RubricCriterion] = Field(min_length=1)


class EvaluationCase(BaseModel):
    """One scripted Tutor scenario: a handoff plus the rubric it should be graded against.

    Lives as data under evaluation/cases/; a case moves to
    evaluation/locked/ once frozen for regression tracking (see
    LockedCaseStore below).
    """

    case_id: str
    handoff: CoordinatorToTutorHandoff
    rubric_id: str
    notes: Optional[str] = None


class CriterionScore(BaseModel):
    criterion_id: str
    passed: bool
    rationale: Optional[str] = None


class EvaluationResult(BaseModel):
    """Outcome of grading one TutorToCoordinatorResult against one Rubric."""

    case_id: str
    rubric_id: str
    rubric_version: int = Field(ge=1)
    criterion_scores: list[CriterionScore] = Field(min_length=1)
    evaluated_at: datetime

    @property
    def passed(self) -> bool:
        return all(score.passed for score in self.criterion_scores)


class LockedCaseStore(Protocol):
    """Read-only access to frozen evaluation cases under evaluation/locked/.

    A locked case's handoff and rubric_id must never change once frozen
    — that is what makes evaluation runs comparable over time; this
    interface has no write method for that reason.
    """

    def list_cases(self) -> list[EvaluationCase]:
        ...

    def get_case(self, case_id: str) -> EvaluationCase:
        ...


class TutorBehaviorEvaluator(Protocol):
    """Grades one Tutor result against one case's rubric.

    Never calls the Tutor itself — result is already-produced output
    (captured from a real turn, or replayed from a locked case) so
    evaluation stays deterministic and reproducible.
    """

    def evaluate(self, case: EvaluationCase, result: TutorToCoordinatorResult) -> EvaluationResult:
        ...
