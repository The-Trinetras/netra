"""Model output is untrusted; support claims are verified against validated evidence."""

import json

import pytest

from netra_api.content.retrieval.evidence import EvidenceTrust
from netra_api.coordinator.decisions import Assessment, InvalidDecisionError, Requirement, parse_decision
from netra_api.coordinator.evidence_check import EvidenceLedger
from netra_api.coordinator.providers.gemini import ModelDecision, ToolCallRequest
from netra_api.coordinator.tool_registry import ToolEvidence, ToolObservation
from netra_api.multimedia.evidence import ObservationSource


def _decision(payload=None, calls=()):
    return ModelDecision(raw_text=json.dumps(payload) if payload is not None else "", finish_reason="stop", tool_calls=list(calls))


@pytest.mark.parametrize(
    ("decision", "check"),
    [
        (_decision(), "empty_decision"),
        (ModelDecision(raw_text="Sure! here's the answer", finish_reason="stop"), "decision_not_json"),
        (_decision({"action": "answer", "text": "x", "reasoning": "secret chain of thought"}), "decision_schema_invalid"),
        (_decision({"action": "answer"}), "decision_schema_invalid"),
        (_decision({"action": "call_tools"}), "call_tools_without_tool_calls"),
        (_decision({"action": "answer", "text": "x"}, [ToolCallRequest(tool_name="t", arguments={})]), "tool_calls_with_final_action"),
        (_decision(None, [ToolCallRequest(tool_name="t", arguments={})] * 7), "too_many_tool_calls"),
    ],
)
def test_invalid_model_output_is_rejected_with_a_specific_check(decision, check):
    with pytest.raises(InvalidDecisionError) as raised:
        parse_decision(decision)
    assert raised.value.check == check


def test_bare_tool_calls_are_accepted_as_call_tools():
    parsed = parse_decision(_decision(None, [ToolCallRequest(tool_name="t", arguments={})]))
    assert parsed.payload.action == "call_tools" and len(parsed.tool_calls) == 1


def _ledger(observations=()):
    ledger = EvidenceLedger()
    ledger.add_requirements((Requirement(requirement_id="x-axis", description="x axis"),))
    ledger.add_evidence(
        (ToolEvidence(evidence_id="fig", source_version_id="v", locator="p", text="t", provenance="m3", trust=EvidenceTrust.DERIVED, observations=tuple(observations)),)
    )
    return ledger


@pytest.mark.parametrize(
    ("source", "status", "approximate"),
    [
        (ObservationSource.OBSERVED, "supported", False),
        (ObservationSource.ESTIMATED, "supported", True),
        (ObservationSource.UNREADABLE, "unreadable", False),
        (ObservationSource.GENERATED, "missing", False),
    ],
)
def test_observation_provenance_decides_whether_a_claim_is_supported(source, status, approximate):
    ledger = _ledger([ToolObservation(label="x-axis", value="current", source=source)])
    ledger.apply_assessments((Assessment(requirement_id="x-axis", status="supported", evidence_id="fig", observation_label="X-Axis"),))
    state = ledger.requirements["x-axis"]
    assert (state.status, state.approximate) == (status, approximate)


def test_citing_evidence_that_was_never_validated_is_a_gap():
    ledger = _ledger()
    gaps = ledger.apply_assessments((Assessment(requirement_id="x-axis", status="supported", evidence_id="made-up"),))
    assert gaps[0].gap_code == "cited_evidence_not_validated"


def test_identical_action_is_unproductive_only_while_requirements_remain_open():
    ledger = _ledger()
    ledger.executed_actions.add("search:{}")
    assert ledger.is_unproductive_repeat("search:{}")
    ledger.apply_assessments((Assessment(requirement_id="x-axis", status="supported", evidence_id="fig"),))
    assert not ledger.is_unproductive_repeat("search:{}")
