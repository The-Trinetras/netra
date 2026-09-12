import json
from datetime import datetime, timezone
from pathlib import Path

from interfaces import CriterionScore, EvaluationCase, EvaluationResult, Rubric, RubricCriterion

from netra_api.coordinator.handoff import CoordinatorToTutorHandoff

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_HANDOFF = REPO_ROOT / "shared" / "contracts" / "examples" / "handoffs" / "coordinator_to_tutor.json"


def _load_handoff() -> CoordinatorToTutorHandoff:
    data = json.loads(EXAMPLE_HANDOFF.read_text())
    return CoordinatorToTutorHandoff.model_validate(data)


def test_evaluation_case_wraps_a_real_handoff():
    case = EvaluationCase(case_id="case-1", handoff=_load_handoff(), rubric_id="rubric-1")
    assert case.handoff.mode == "explain"


def test_rubric_requires_at_least_one_criterion():
    rubric = Rubric(
        rubric_id="rubric-1",
        rubric_version=1,
        criteria=[RubricCriterion(criterion_id="cites-evidence", description="Cites at least one evidence id")],
    )
    assert len(rubric.criteria) == 1


def test_evaluation_result_passed_requires_every_criterion_to_pass():
    passing = EvaluationResult(
        case_id="case-1",
        rubric_id="rubric-1",
        rubric_version=1,
        criterion_scores=[CriterionScore(criterion_id="cites-evidence", passed=True)],
        evaluated_at=datetime.now(timezone.utc),
    )
    failing = passing.model_copy(
        update={"criterion_scores": [CriterionScore(criterion_id="cites-evidence", passed=False)]}
    )
    assert passing.passed is True
    assert failing.passed is False
