import json
from pathlib import Path
from uuid import uuid4

import pytest
from deterministic_evaluator import (
    DETERMINISTIC_CRITERIA,
    DeterministicTutorEvaluator,
    UnknownCriterionError,
    UnknownRubricError,
)
from interfaces import EvaluationCase, Rubric, RubricCriterion

from netra_api.coordinator.handoff import CoordinatorToTutorHandoff, TutorToCoordinatorResult

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_HANDOFF = REPO_ROOT / "shared" / "contracts" / "examples" / "handoffs" / "coordinator_to_tutor.json"


def _load_handoff() -> CoordinatorToTutorHandoff:
    data = json.loads(EXAMPLE_HANDOFF.read_text())
    return CoordinatorToTutorHandoff.model_validate(data)


def _case(rubric_id: str = "rubric-1") -> EvaluationCase:
    return EvaluationCase(case_id="case-1", handoff=_load_handoff(), rubric_id=rubric_id)


def _rubric(*criterion_ids: str, rubric_id: str = "rubric-1") -> Rubric:
    return Rubric(
        rubric_id=rubric_id,
        rubric_version=1,
        criteria=[RubricCriterion(criterion_id=cid, description=cid) for cid in criterion_ids],
    )


def _result(**overrides) -> TutorToCoordinatorResult:
    defaults = dict(
        handoff_id=uuid4(),
        status="completed",
        public_segments=[{"kind": "explanation", "text": "Congestion control protects the network."}],
        pending_question_id=None,
        evidence_ids=["ev-27"],
        proposed_learning_events=[{"event_type": "concept_exposed", "concept_id": "concept-congestion-control"}],
    )
    defaults.update(overrides)
    return TutorToCoordinatorResult(**defaults)


def test_all_deterministic_checks_pass_for_a_well_formed_result():
    case = _case()
    rubric = _rubric(*DETERMINISTIC_CRITERIA.keys())
    evaluator = DeterministicTutorEvaluator(rubrics={"rubric-1": rubric})

    result = evaluator.evaluate(case, _result())

    assert result.passed is True
    assert {score.criterion_id for score in result.criterion_scores} == set(DETERMINISTIC_CRITERIA)


def test_cites_evidence_fails_when_no_evidence_ids():
    case = _case()
    rubric = _rubric("cites-evidence")
    evaluator = DeterministicTutorEvaluator(rubrics={"rubric-1": rubric})

    result = evaluator.evaluate(case, _result(evidence_ids=[]))

    assert result.passed is False
    assert result.criterion_scores[0].criterion_id == "cites-evidence"


def test_evidence_ids_authorized_fails_for_an_uncited_reference():
    case = _case()
    rubric = _rubric("evidence-ids-authorized")
    evaluator = DeterministicTutorEvaluator(rubrics={"rubric-1": rubric})

    result = evaluator.evaluate(case, _result(evidence_ids=["ev-99"]))  # not in the handoff's evidence_refs

    assert result.passed is False


def test_responded_fails_for_completed_status_with_no_segments():
    case = _case()
    rubric = _rubric("responded")
    evaluator = DeterministicTutorEvaluator(rubrics={"rubric-1": rubric})

    result = evaluator.evaluate(case, _result(status="completed", public_segments=[]))

    assert result.passed is False


def test_responded_is_waived_for_a_failed_result():
    case = _case()
    rubric = _rubric("responded")
    evaluator = DeterministicTutorEvaluator(rubrics={"rubric-1": rubric})

    result = evaluator.evaluate(
        case, _result(status="failed", public_segments=[], proposed_learning_events=[])
    )

    assert result.passed is True


def test_pending_question_consistency_fails_when_status_and_field_disagree():
    case = _case()
    rubric = _rubric("pending-question-consistency")
    evaluator = DeterministicTutorEvaluator(rubrics={"rubric-1": rubric})

    result = evaluator.evaluate(case, _result(status="awaiting_student_answer", pending_question_id=None))

    assert result.passed is False


def test_learning_events_outside_target_concepts_fail():
    case = _case()
    rubric = _rubric("learning-events-target-handoff-concepts")
    evaluator = DeterministicTutorEvaluator(rubrics={"rubric-1": rubric})

    result = evaluator.evaluate(
        case,
        _result(proposed_learning_events=[{"event_type": "concept_exposed", "concept_id": "concept-unrelated"}]),
    )

    assert result.passed is False


def test_unknown_rubric_id_fails_closed():
    case = _case(rubric_id="does-not-exist")
    evaluator = DeterministicTutorEvaluator(rubrics={})

    with pytest.raises(UnknownRubricError):
        evaluator.evaluate(case, _result())


RUBRICS_DIR = REPO_ROOT / "evaluation" / "rubrics"
CASES_DIR = REPO_ROOT / "evaluation" / "cases"


def test_committed_tutor_core_rubric_loads_and_every_criterion_is_registered():
    """The shipped rubric may only reference checks that actually exist —
    otherwise a run would fail closed at grading time instead of here."""

    rubric = Rubric.model_validate(json.loads((RUBRICS_DIR / "tutor_core.json").read_text()))

    assert rubric.rubric_id == "tutor-core"
    for criterion in rubric.criteria:
        assert criterion.criterion_id in DETERMINISTIC_CRITERIA


def test_committed_ohms_law_case_grades_against_the_committed_rubric():
    """End-to-end over real data files: the Ohm's Law case, the shipped
    rubric, and a result that cites the case's own evidence."""

    case = EvaluationCase.model_validate(
        json.loads((CASES_DIR / "ohms_law_explain_case.json").read_text())
    )
    rubric = Rubric.model_validate(json.loads((RUBRICS_DIR / "tutor_core.json").read_text()))
    evaluator = DeterministicTutorEvaluator(rubrics={rubric.rubric_id: rubric})

    graded = evaluator.evaluate(
        case,
        TutorToCoordinatorResult(
            handoff_id=uuid4(),
            status="completed",
            public_segments=[
                {
                    "kind": "explanation",
                    "text": "The graph puts current on the x-axis and voltage on the y-axis, "
                    "and the line rises 2 volts per ampere.",
                }
            ],
            evidence_ids=["ev-ohm-graph", "ev-ohm-equation"],
            proposed_learning_events=[
                {"event_type": "concept_exposed", "concept_id": "concept-ohms-law"}
            ],
        ),
    )

    assert graded.passed is True
    assert graded.rubric_version == rubric.rubric_version


def test_ohms_law_source_fixture_matches_the_spec_acceptance_values():
    """Guards the fixture against drift from the AgentSpec values that
    docs/team/integration-checklist.md pins: (1 A, 2 V), (2 A, 4 V),
    (3 A, 6 V), current on x, voltage on y, V = I x R."""

    fixture = json.loads((CASES_DIR / "ohms_law_source.json").read_text())
    by_id = {item["evidence_id"]: item["text"] for item in fixture["evidence"]}

    assert fixture["fixture_kind"] == "source_evidence"
    for pair in ("1 A, 2 V", "2 A, 4 V", "3 A, 6 V"):
        assert pair in by_id["ev-ohm-table"]
    assert "current on the horizontal x-axis" in by_id["ev-ohm-graph"]
    assert "voltage on the vertical y-axis" in by_id["ev-ohm-graph"]
    assert "V = I x R" in by_id["ev-ohm-equation"]


def test_unregistered_criterion_id_fails_closed():
    case = _case()
    rubric = _rubric("no-such-check")
    evaluator = DeterministicTutorEvaluator(rubrics={"rubric-1": rubric})

    with pytest.raises(UnknownCriterionError):
        evaluator.evaluate(case, _result())
