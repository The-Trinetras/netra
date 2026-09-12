import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from netra_api.coordinator.handoff import CoordinatorToTutorHandoff, TutorToCoordinatorResult

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLES_DIR = REPO_ROOT / "shared" / "contracts" / "examples" / "handoffs"


def test_coordinator_to_tutor_example_is_valid():
    data = json.loads((EXAMPLES_DIR / "coordinator_to_tutor.json").read_text())
    handoff = CoordinatorToTutorHandoff.model_validate(data)
    assert handoff.mode == "explain"
    assert handoff.evidence_refs[0].evidence_id == "ev-27"


def test_tutor_to_coordinator_example_is_valid():
    data = json.loads((EXAMPLES_DIR / "tutor_to_coordinator.json").read_text())
    result = TutorToCoordinatorResult.model_validate(data)
    assert result.status == "completed"
    assert result.public_segments[0].kind == "explanation"


def test_handoff_rejects_duplicate_target_concept_ids():
    data = json.loads((EXAMPLES_DIR / "coordinator_to_tutor.json").read_text())
    data["target_concept_ids"] = ["concept-a", "concept-a"]
    with pytest.raises(ValidationError):
        CoordinatorToTutorHandoff.model_validate(data)


def test_handoff_rejects_unknown_mode():
    data = json.loads((EXAMPLES_DIR / "coordinator_to_tutor.json").read_text())
    data["mode"] = "not_a_real_mode"
    with pytest.raises(ValidationError):
        CoordinatorToTutorHandoff.model_validate(data)
