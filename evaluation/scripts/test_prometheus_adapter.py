import pytest

from prometheus_adapter import (
    Prometheus2EvaluationAdapter,
    PrometheusEvaluationCase,
    load_answer_cases,
    package_instruction,
)


def make_case(**updates):
    values = dict(
        case_id="case-1", dataset_id="dataset", dataset_version="1",
        question="What is the answer?", reference_answer="The answer is evidence.",
        reference_contexts=("Reference context",), retrieved_contexts=("Retrieved context",),
        retrieved_chunk_ids=("chunk-1",), rubric_id="rubric", rubric_version="1",
        evaluator_model="prometheus-eval/prometheus-7b-v2.0",
        source_version_id="version-1",
    )
    values.update(updates)
    return PrometheusEvaluationCase(**values)


class FakeJudge:
    def __init__(self):
        self.absolute = None
        self.relative = None

    def single_absolute_grade(self, **kwargs):
        self.absolute = kwargs
        return "grounded", 5

    def single_relative_grade(self, **kwargs):
        self.relative = kwargs
        return "A is better", "A"


def test_instruction_separates_question_reference_and_retrieved_evidence():
    instruction = package_instruction(make_case())
    assert "QUESTION:\nWhat is the answer?" in instruction
    assert "REFERENCE ANSWER:\nThe answer is evidence." in instruction
    assert "[Evidence 1]\nRetrieved context" in instruction
    assert "chunk-1" not in instruction


def test_absolute_mapping_and_result_normalization():
    judge = FakeJudge()
    result = Prometheus2EvaluationAdapter(judge, rubric_text="rubric text").absolute_grade(make_case(), "Generated answer")
    assert judge.absolute["response"] == "Generated answer"
    assert judge.absolute["reference_answer"] == "The answer is evidence."
    assert judge.absolute["rubric"] == "rubric text"
    assert result.score == 5 and result.rationale == "grounded"
    assert result.rubric_id == "rubric" and result.rubric_version == "1"


def test_relative_mapping_preserves_winner_and_rationale():
    judge = FakeJudge()
    result = Prometheus2EvaluationAdapter(judge, rubric_text="rubric text").relative_grade(make_case(), "A", "B")
    assert judge.relative["response_A"] == "A" and judge.relative["response_B"] == "B"
    assert result.winner == "A" and result.rationale == "A is better"


def test_empty_evidence_is_explicit_and_empty_answer_fails():
    case = make_case(reference_contexts=(), retrieved_contexts=(), retrieved_chunk_ids=())
    instruction = package_instruction(case)
    assert "[NO REFERENCE EVIDENCE]" in instruction
    assert "[NO RETRIEVED EVIDENCE]" in instruction
    with pytest.raises(ValueError, match="generated_answer"):
        Prometheus2EvaluationAdapter(FakeJudge(), rubric_text="rubric").absolute_grade(case, " ")


def test_malformed_cases_fail_clearly():
    with pytest.raises(ValueError):
        make_case(question=" ")


def test_real_p3_dataset_is_versioned_and_deterministic():
    cases = load_answer_cases("evaluation/cases/netra_p3_answer_golden_v1.jsonl")
    assert len(cases) == 6
    assert [case.case_id for case in cases] == sorted(case.case_id for case in cases)
    assert all(case.rubric_id == "netra_answer_groundedness_v1" for case in cases)
    assert all(case.source_version_id == "60610bd7-cd97-478e-84bd-82b13235b7ec" for case in cases)
