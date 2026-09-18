"""Deterministic Tutor-behavior evaluator.

learning.md "Evaluation": "Prioritize deterministic source/evidence
checks, original-media review, state and recovery tests and
accessibility tasks. Ragas and Prometheus-2 are secondary evaluators;
their integration remains pending." docs/team/M4.md "Acceptance
target": "Prioritize deterministic checks and original-source review;
Ragas/Prometheus-2 are secondary evaluators outside the live deadline."

This module is that first, required layer: it structurally implements
evaluation.scripts.interfaces.TutorBehaviorEvaluator using only
structural checks against an already-produced
netra_api.coordinator.handoff.TutorToCoordinatorResult and the
EvaluationCase's own handoff — no model call, no network access, no
provider SDK. Ragas/Prometheus-2 integration is explicitly out of scope
here (runtime-baseline.md: "Do not install... without explicit
approval"; "No evaluator network calls or model downloads without
explicit authorization") and remains unimplemented.

interfaces.TutorBehaviorEvaluator.evaluate(case, result) has no rubric
parameter — a Rubric is data living under evaluation/rubrics/, not part
of the typed interface — so DeterministicTutorEvaluator is constructed
with a rubric lookup instead of receiving one per call.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Mapping, Tuple

from interfaces import CriterionScore, EvaluationCase, EvaluationResult, Rubric

from netra_api.coordinator.handoff import TutorToCoordinatorResult
from netra_api.platform.errors import NetraError

CriterionCheck = Callable[[EvaluationCase, TutorToCoordinatorResult], Tuple[bool, str]]
"""One deterministic, structural check. Returns (passed, rationale) —
rationale is always populated (CriterionScore.rationale), never left to
imply an unexplained pass/fail."""


class UnknownRubricError(NetraError):
    """Raised when an EvaluationCase.rubric_id has no entry in the
    evaluator's rubric lookup."""

    def __init__(self, rubric_id: str) -> None:
        self.rubric_id = rubric_id
        super().__init__(f"no rubric registered for rubric_id {rubric_id!r}")


class UnknownCriterionError(NetraError):
    """Raised when a Rubric references a criterion_id this evaluator has
    no deterministic check for.

    Fails closed rather than silently skipping or auto-passing an
    unrecognised criterion (CLAUDE.md: "Unimplemented ... must fail
    closed, never return success") — a rubric criterion with no
    registered check is a configuration gap, not evidence the Tutor
    behaved correctly.
    """

    def __init__(self, criterion_id: str) -> None:
        self.criterion_id = criterion_id
        super().__init__(f"no deterministic check registered for criterion_id {criterion_id!r}")


def _check_cites_evidence(case: EvaluationCase, result: TutorToCoordinatorResult) -> Tuple[bool, str]:
    """The result must cite at least one evidence_id.

    A Tutor response that explains or answers without citing any
    evidence is exactly the "invented detail" current-scope.md warns
    against for an unresolved evidence gap — this is a structural proxy
    for "the response is grounded in something," not a claim that the
    citation is semantically correct (message-flow.md: "A valid citation
    alone does not prove semantic support").
    """

    ok = len(result.evidence_ids) > 0
    return ok, (
        f"cites {len(result.evidence_ids)} evidence id(s)"
        if ok
        else "result.evidence_ids is empty"
    )


def _check_evidence_ids_are_authorized(case: EvaluationCase, result: TutorToCoordinatorResult) -> Tuple[bool, str]:
    """Every cited evidence_id must be one the handoff actually authorized.

    CLAUDE.md: "Validate vector references against PostgreSQL before
    supplying evidence to models" — this evaluator cannot re-run that
    authorization check itself (it never touches PostgreSQL), but it can
    at least catch a Tutor citing an evidence_id nobody handed it, which
    is a stronger observable failure than an unauthorized-but-cited id.
    """

    allowed = {ref.evidence_id for ref in case.handoff.evidence_refs}
    cited = set(result.evidence_ids)
    unauthorized = cited - allowed
    ok = not unauthorized
    return ok, (
        "every cited evidence_id was in the handoff's evidence_refs"
        if ok
        else f"cited evidence_id(s) not in the handoff: {sorted(unauthorized)}"
    )


def _check_responded(case: EvaluationCase, result: TutorToCoordinatorResult) -> Tuple[bool, str]:
    """A non-failed result must actually deliver at least one public segment.

    Catches the degenerate case of status="completed" with no content —
    generation of a question or mention of a concept is not itself
    assessment evidence (learning.md), and an empty response is not
    "answered the question" either.
    """

    if result.status == "failed":
        return True, "status is failed; no delivered-content check applies"
    ok = len(result.public_segments) > 0
    return ok, (
        f"delivered {len(result.public_segments)} public segment(s)"
        if ok
        else f"status is {result.status!r} but public_segments is empty"
    )


def _check_pending_question_consistency(
    case: EvaluationCase, result: TutorToCoordinatorResult
) -> Tuple[bool, str]:
    """status == "awaiting_student_answer" iff a pending_question_id is set.

    learning.md: "Persist the pending question before delivering it to
    the client." A result that claims to be waiting on the student
    without a question to answer (or vice versa) is an internally
    inconsistent handoff result, independent of any provider judgment.
    """

    is_awaiting = result.status == "awaiting_student_answer"
    has_pending = result.pending_question_id is not None
    ok = is_awaiting == has_pending
    return ok, (
        "status/pending_question_id agree"
        if ok
        else f"status={result.status!r} but pending_question_id={result.pending_question_id!r}"
    )


def _check_learning_events_reference_target_concepts(
    case: EvaluationCase, result: TutorToCoordinatorResult
) -> Tuple[bool, str]:
    """Every proposed learning event's concept_id must be one of the
    handoff's target_concept_ids.

    A guard against the Tutor silently creating concepts or drifting onto
    an unrelated concept (agent-boundaries.md: "Tutor cannot directly
    assign mastery, invent concepts/prerequisites ... Do not silently
    create concepts or prerequisite relationships from Tutor output").
    """

    allowed = set(case.handoff.target_concept_ids)
    offending = [
        event.concept_id for event in result.proposed_learning_events if event.concept_id not in allowed
    ]
    ok = not offending
    return ok, (
        "every proposed learning event targets a concept from the handoff"
        if ok
        else f"proposed event(s) target concept(s) outside target_concept_ids: {offending}"
    )


DETERMINISTIC_CRITERIA: Mapping[str, CriterionCheck] = {
    "cites-evidence": _check_cites_evidence,
    "evidence-ids-authorized": _check_evidence_ids_are_authorized,
    "responded": _check_responded,
    "pending-question-consistency": _check_pending_question_consistency,
    "learning-events-target-handoff-concepts": _check_learning_events_reference_target_concepts,
}
"""The fixed registry of criterion_id -> deterministic check.

A Rubric under evaluation/rubrics/ may only reference criterion_ids
present here (or in a caller-supplied extension mapping) — see
UnknownCriterionError. Deliberately not a plugin/discovery mechanism:
criterion_id vocabulary is reviewed the same as any other product
policy, not extended by dropping a new rubric file with an untested id.
"""


class DeterministicTutorEvaluator:
    """Structurally implements
    evaluation.scripts.interfaces.TutorBehaviorEvaluator.

    Grades an already-produced TutorToCoordinatorResult against the
    Rubric registered for case.rubric_id, running only the fixed,
    deterministic checks in DETERMINISTIC_CRITERIA (or criteria, when a
    caller supplies an extended/overriding mapping). Never calls the
    Tutor, a provider, or the network.
    """

    def __init__(
        self,
        rubrics: Mapping[str, Rubric],
        criteria: Mapping[str, CriterionCheck] = DETERMINISTIC_CRITERIA,
    ) -> None:
        self._rubrics = rubrics
        self._criteria = criteria

    def evaluate(self, case: EvaluationCase, result: TutorToCoordinatorResult) -> EvaluationResult:
        rubric = self._rubrics.get(case.rubric_id)
        if rubric is None:
            raise UnknownRubricError(case.rubric_id)

        scores = []
        for criterion in rubric.criteria:
            check = self._criteria.get(criterion.criterion_id)
            if check is None:
                raise UnknownCriterionError(criterion.criterion_id)
            passed, rationale = check(case, result)
            scores.append(CriterionScore(criterion_id=criterion.criterion_id, passed=passed, rationale=rationale))

        return EvaluationResult(
            case_id=case.case_id,
            rubric_id=rubric.rubric_id,
            rubric_version=rubric.rubric_version,
            criterion_scores=scores,
            evaluated_at=datetime.now(timezone.utc),
        )
