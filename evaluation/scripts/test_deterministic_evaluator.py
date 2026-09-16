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


def test_unregistered_criterion_id_fails_closed():
    case = _case()
    rubric = _rubric("no-such-check")
    evaluator = DeterministicTutorEvaluator(rubrics={"rubric-1": rubric})

    with pytest.raises(UnknownCriterionError):
        evaluator.evaluate(case, _result())
