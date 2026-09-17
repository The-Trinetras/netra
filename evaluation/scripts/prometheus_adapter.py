"""Evaluation-only Prometheus-2 adapter and answer-level data contract.

This module deliberately does not import ``prometheus_eval`` at module load
time.  A judge is injected by the isolated evaluation environment, keeping
Prometheus out of Netra's API, worker, and normal test runtime.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PrometheusJudge(Protocol):
    def single_absolute_grade(
        self, instruction: str, response: str, rubric: str,
        reference_answer: str | None = None,
    ) -> tuple[str, int]: ...

    def single_relative_grade(
        self, instruction: str, response_A: str, response_B: str, rubric: str,
        reference_answer: str | None = None,
    ) -> tuple[str, int]: ...


class PrometheusEvaluationCase(BaseModel):
    """Immutable answer-level case containing only evaluation data."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    dataset_id: str
    dataset_version: str
    question: str
    reference_answer: str
    reference_contexts: tuple[str, ...] = ()
    retrieved_contexts: tuple[str, ...] = ()
    retrieved_chunk_ids: tuple[str, ...] = ()
    rubric_id: str
    rubric_version: str
    evaluator_model: str
    source_version_id: str

    @field_validator(
        "case_id", "dataset_id", "dataset_version", "question", "reference_answer",
        "rubric_id", "rubric_version", "evaluator_model", "source_version_id",
    )
    @classmethod
    def nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value

    @field_validator("reference_contexts", "retrieved_contexts", "retrieved_chunk_ids")
    @classmethod
    def valid_items(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value.strip() for value in values):
            raise ValueError("items must not be empty")
        return values


class PrometheusEvaluationResult(BaseModel):
    """Normalized result preserving the evaluator's native 1--5 score."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    evaluator: str = "prometheus-2"
    evaluator_model: str
    rubric_id: str
    rubric_version: str
    score: int | None = Field(default=None, ge=1, le=5)
    rationale: str
    winner: str | None = None


def package_instruction(case: PrometheusEvaluationCase) -> str:
    """Build the evaluator instruction without exposing IDs or app state."""

    reference = "\n\n".join(case.reference_contexts) or "[NO REFERENCE EVIDENCE]"
    retrieved = "\n\n".join(
        f"[Evidence {index}]\n{context}"
        for index, context in enumerate(case.retrieved_contexts, 1)
    ) or "[NO RETRIEVED EVIDENCE]"
    return (
        "QUESTION:\n"
        f"{case.question}\n\n"
        "REFERENCE ANSWER:\n"
        f"{case.reference_answer}\n\n"
        "REFERENCE CONTEXT:\n"
        f"{reference}\n\n"
        "RETRIEVED EVIDENCE:\n"
        f"{retrieved}\n\n"
        "Judge grounding using only the supplied retrieved evidence. "
        "Do not treat evidence labels as substantive content."
    )


class Prometheus2EvaluationAdapter:
    """Thin adapter around the verified Prometheus-2 judge API."""

    def __init__(self, judge: PrometheusJudge, *, rubric_text: str) -> None:
        self._judge = judge
        self._rubric_text = rubric_text

    def absolute_grade(
        self, case: PrometheusEvaluationCase, generated_answer: str,
    ) -> PrometheusEvaluationResult:
        if not generated_answer.strip():
            raise ValueError("generated_answer must not be empty")
        rationale, score = self._judge.single_absolute_grade(
            instruction=package_instruction(case), response=generated_answer,
            rubric=self._rubric_text, reference_answer=case.reference_answer,
        )
        return PrometheusEvaluationResult(
            case_id=case.case_id, evaluator_model=case.evaluator_model,
            rubric_id=case.rubric_id, rubric_version=case.rubric_version,
            score=score, rationale=rationale,
        )

    def relative_grade(
        self, case: PrometheusEvaluationCase, response_a: str, response_b: str,
    ) -> PrometheusEvaluationResult:
        if not response_a.strip() or not response_b.strip():
            raise ValueError("pairwise responses must not be empty")
        rationale, winner = self._judge.single_relative_grade(
            instruction=package_instruction(case), response_A=response_a,
            response_B=response_b, rubric=self._rubric_text,
            reference_answer=case.reference_answer,
        )
        if winner not in ("A", "B"):
            raise ValueError("Prometheus pairwise result must be A or B")
        return PrometheusEvaluationResult(
            case_id=case.case_id, evaluator_model=case.evaluator_model,
            rubric_id=case.rubric_id, rubric_version=case.rubric_version,
            rationale=rationale, winner=winner,
        )


def load_answer_cases(path: str | Path) -> list[PrometheusEvaluationCase]:
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"dataset does not exist: {path}")
    cases: list[PrometheusEvaluationCase] = []
    identities: set[tuple[str, str, str]] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            raise ValueError(f"blank dataset line at {line_number}")
        try:
            case = PrometheusEvaluationCase.model_validate(json.loads(line))
        except (ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid case at line {line_number}: {exc}") from exc
        identity = (case.dataset_id, case.dataset_version, case.case_id)
        if identity in identities:
            raise ValueError(f"duplicate case identity at line {line_number}")
        identities.add(identity)
        cases.append(case)
    return sorted(cases, key=lambda case: case.case_id)
