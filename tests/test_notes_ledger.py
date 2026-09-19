"""The evidence ledger: what code verifies about a draft, and what it cannot.

Ported behaviour from the earlier project's evidence ledger: a model may claim a
requirement is supported, but the claim only counts if the cited evidence was really
retrieved. No model, no key, no database here.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from demo.notes.flow import objections_from_problems
from demo.notes.ledger import Evidence, EvidenceLedger
from demo.notes.schema import AnswerDraft

E1 = Evidence("ev-1", "ohms-law-notes.md", 0, "R = V / I = 6 / 3 = 2 ohms")
E2 = Evidence("ev-2", "series-circuits-notes.md", 0, "Total resistance is the sum.")


def _draft(**overrides):
    base = {
        "action": "answer",
        "requirements": [{"requirement_id": "r1", "description": "resistance at 6 V and 3 A"}],
        "assessments": [{"requirement_id": "r1", "status": "supported", "evidence_id": "ev-1"}],
        "text": "The resistance is 2 ohms.",
        "cited_evidence_ids": ["ev-1"],
    }
    base.update(overrides)
    return AnswerDraft.model_validate(base)


def _ledger():
    ledger = EvidenceLedger()
    ledger.add_evidence([E1, E2])
    return ledger


# ------------------------------------------------------------------ passing

def test_a_supported_answer_citing_real_evidence_has_no_problems():
    assert _ledger().check_draft(_draft()) == []


def test_a_stated_gap_with_a_missing_requirement_has_no_problems():
    draft = _draft(action="state_gap", cited_evidence_ids=[], text="The notes do not cover that.",
                   assessments=[{"requirement_id": "r1", "status": "missing",
                                 "gap": "the notes do not cover parallel circuits"}])
    assert _ledger().check_draft(draft) == []


# ---------------------------------------------------------------- citations

def test_citing_an_id_that_was_never_retrieved_is_rejected():
    problems = _ledger().check_draft(_draft(cited_evidence_ids=["ev-made-up"]))
    assert "cited_evidence_not_validated:ev-made-up" in problems


def test_a_supported_claim_that_cites_unretrieved_evidence_becomes_a_gap():
    draft = _draft(assessments=[{"requirement_id": "r1", "status": "supported",
                                 "evidence_id": "ev-made-up"}])
    ledger = _ledger()
    problems = ledger.check_draft(draft)
    assert ledger.requirements["r1"].status == "missing"
    assert ledger.requirements["r1"].gap_code == "cited_evidence_not_validated"
    assert any(p.startswith("requirement_unresolved:r1:cited_evidence_not_validated") for p in problems)


def test_an_answer_that_cites_nothing_is_rejected():
    problems = _ledger().check_draft(_draft(cited_evidence_ids=[]))
    assert "no_validated_evidence_cited" in problems


def test_a_supported_claim_with_no_evidence_id_is_not_supported():
    draft = _draft(assessments=[{"requirement_id": "r1", "status": "supported"}])
    problems = _ledger().check_draft(draft)
    assert any(p.startswith("requirement_unresolved:r1") for p in problems)


# ------------------------------------------------------------- requirements

def test_a_requirement_nobody_assessed_blocks_an_answer():
    draft = _draft(assessments=[])
    problems = _ledger().check_draft(draft)
    assert "requirement_unresolved:r1:not_assessed" in problems


def test_a_requirement_reported_missing_blocks_an_answer():
    draft = _draft(assessments=[{"requirement_id": "r1", "status": "missing", "gap": "not in notes"}])
    problems = _ledger().check_draft(draft)
    assert "requirement_unresolved:r1:reported_missing" in problems


def test_assessing_an_undeclared_requirement_is_a_problem_not_silently_ignored():
    draft = _draft(assessments=[
        {"requirement_id": "r1", "status": "supported", "evidence_id": "ev-1"},
        {"requirement_id": "ghost", "status": "supported", "evidence_id": "ev-1"}])
    problems = _ledger().check_draft(draft)
    assert "assessment_for_undeclared_requirement:ghost" in problems


def test_stating_a_gap_when_everything_is_supported_is_rejected():
    draft = _draft(action="state_gap", cited_evidence_ids=[], text="I cannot say.")
    problems = _ledger().check_draft(draft)
    assert "gap_stated_but_every_requirement_supported" in problems


# --------------------------------------------------------------- the schema

def test_a_stated_gap_may_not_cite_evidence():
    with pytest.raises(ValidationError):
        _draft(action="state_gap", cited_evidence_ids=["ev-1"], text="no")


def test_the_draft_schema_forbids_extra_fields():
    with pytest.raises(ValidationError):
        _draft(reasoning="my private thoughts")


def test_a_draft_needs_at_least_one_requirement():
    with pytest.raises(ValidationError):
        _draft(requirements=[])


# ------------------------------------------------------ objections for drafter

def test_ledger_codes_become_objections_the_drafter_can_act_on():
    problems = ["cited_evidence_not_validated:ev-made-up",
                "requirement_unresolved:r1:reported_missing",
                "no_validated_evidence_cited"]
    objections = objections_from_problems(problems)
    assert "ev-made-up" in objections[0].problem
    assert objections[1].requirement_id == "r1" and "reported_missing" in objections[1].problem
    assert "cites no evidence" in objections[2].problem
