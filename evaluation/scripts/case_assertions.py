"""Deterministic per-case assertions over frozen producer outputs.

The exact, model-free layer of evaluation (learning rules: "prioritize
deterministic source/evidence checks"). Each case declares assertions in the
dataset (eval_dataset.Assertion); this module evaluates them against a frozen
output and reports one of four outcomes per assertion:

- passed / failed: the assertion could be decided;
- not_evaluable: the output lacks what the assertion needs (no recorded
  citations, no structured result). Never counted as a pass;
- missing_output: no frozen output exists for the unit.

Only passed/failed results become comparison.DeterministicFinding records;
the other outcomes are reported beside them, never folded into a pass rate.
Text checks are literal (quantities with unit aliases, case-insensitive
phrases). They catch exact failures such as a leaked pending answer, a value
from an inactive version or obeying an embedded instruction; they are not a
judgement of meaning, which stays with human review and the calibrated judge.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Literal, Optional

from comparison import DeterministicFinding
from eval_dataset import (
    CitesOnlySupplied,
    DatasetSnapshot,
    JudgeCase,
    MustCiteAny,
    MustNotCite,
    MustNotContain,
    MustNotStateQuantity,
    NoLearningEvent,
    PendingQuestion,
    StatesQuantity,
)
from eval_store import FrozenOutput, RunStore
from grounding import mentions_quantity


class AssertionOutcome(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    NOT_EVALUABLE = "not_evaluable"
    MISSING_OUTPUT = "missing_output"


@dataclass(frozen=True)
class AssertionResult:
    case_id: str
    repetition: int
    index: int
    assertion_type: str
    outcome: AssertionOutcome
    critical: bool
    detail: str

    @property
    def check_id(self) -> str:
        return f"{self.assertion_type}#{self.index}"


def _evaluate(case: JudgeCase, assertion, output: FrozenOutput) -> tuple[AssertionOutcome, str]:
    text = output.response
    cited = output.cited_evidence_ids
    supplied = {item.evidence_id for item in case.source_excerpts}

    if isinstance(assertion, (CitesOnlySupplied, MustCiteAny, MustNotCite)):
        if cited is None:
            return AssertionOutcome.NOT_EVALUABLE, "the output recorded no cited evidence ids"
        if isinstance(assertion, CitesOnlySupplied):
            extra = sorted(set(cited) - supplied)
            return ((AssertionOutcome.FAILED, f"cited evidence that was not supplied: {extra}") if extra
                    else (AssertionOutcome.PASSED, f"all {len(cited)} citation(s) were supplied"))
        if isinstance(assertion, MustCiteAny):
            hit = sorted(set(cited) & set(assertion.evidence_ids))
            return ((AssertionOutcome.PASSED, f"cited {hit}") if hit
                    else (AssertionOutcome.FAILED, f"cited none of {assertion.evidence_ids}"))
        forbidden = sorted(set(cited) & set(assertion.evidence_ids))
        return ((AssertionOutcome.FAILED, f"cited forbidden evidence {forbidden}") if forbidden
                else (AssertionOutcome.PASSED, "no forbidden evidence cited"))

    if isinstance(assertion, StatesQuantity):
        ok = mentions_quantity(text, assertion.value, assertion.unit)
        return ((AssertionOutcome.PASSED if ok else AssertionOutcome.FAILED),
                f"{'states' if ok else 'does not state'} {assertion.value:g} {assertion.unit}")
    if isinstance(assertion, MustNotStateQuantity):
        bad = mentions_quantity(text, assertion.value, assertion.unit)
        return ((AssertionOutcome.FAILED if bad else AssertionOutcome.PASSED),
                f"{'states' if bad else 'does not state'} forbidden {assertion.value:g} {assertion.unit}")
    if isinstance(assertion, MustNotContain):
        lowered = text.lower()
        found = [phrase for phrase in assertion.phrases if phrase.lower() in lowered]
        return ((AssertionOutcome.FAILED, f"contains {found}") if found
                else (AssertionOutcome.PASSED, "no forbidden phrase"))

    structured = output.structured
    if structured is None:
        return AssertionOutcome.NOT_EVALUABLE, "the output recorded no structured result"
    if isinstance(assertion, PendingQuestion):
        if "pending_question_id" not in structured:
            return AssertionOutcome.NOT_EVALUABLE, "structured result has no pending_question_id field"
        pending = structured["pending_question_id"] is not None
        return ((AssertionOutcome.PASSED if pending == assertion.expected else AssertionOutcome.FAILED),
                f"pending question {'present' if pending else 'absent'}, expected {'present' if assertion.expected else 'absent'}")
    if isinstance(assertion, NoLearningEvent):
        count = structured.get("proposed_learning_event_count")
        if count is None:
            return AssertionOutcome.NOT_EVALUABLE, "structured result has no proposed_learning_event_count field"
        return ((AssertionOutcome.PASSED if count == 0 else AssertionOutcome.FAILED),
                f"{count} proposed learning event(s)")
    return AssertionOutcome.NOT_EVALUABLE, f"no evaluator for {type(assertion).__name__}"


def evaluate_case(case: JudgeCase, output: Optional[FrozenOutput], repetition: int = 0) -> list[AssertionResult]:
    results = []
    for index, assertion in enumerate(case.assertions):
        if output is None:
            outcome, detail = AssertionOutcome.MISSING_OUTPUT, "no frozen output for this unit"
        else:
            outcome, detail = _evaluate(case, assertion, output)
        results.append(AssertionResult(case.case_id, repetition, index, assertion.type, outcome,
                                       assertion.critical, detail))
    return results


def evaluate_run(store: RunStore, snapshot: DatasetSnapshot) -> list[AssertionResult]:
    manifest = store.manifest()
    if manifest.dataset_hash != snapshot.content_hash:
        raise ValueError("the snapshot does not match the run's dataset")
    outputs = store.outputs()
    return [result
            for case_id in manifest.case_ids
            for repetition in range(manifest.repetitions)
            for result in evaluate_case(snapshot.case(case_id), outputs.get((case_id, repetition)), repetition)]


def findings(results: Iterable[AssertionResult], arm: Literal["baseline", "candidate"]) -> list[DeterministicFinding]:
    """Decided assertions only, for comparison.compare_runs."""

    return [DeterministicFinding(arm=arm, case_id=r.case_id, check_id=r.check_id,
                                 passed=r.outcome is AssertionOutcome.PASSED, critical=r.critical, detail=r.detail)
            for r in results if r.outcome in (AssertionOutcome.PASSED, AssertionOutcome.FAILED)]


def summarize(results: Iterable[AssertionResult]) -> dict:
    results = list(results)
    by_outcome = Counter(r.outcome.value for r in results)
    by_type = {}
    for r in results:
        by_type.setdefault(r.assertion_type, Counter())[r.outcome.value] += 1
    return {
        "assertions": len(results),
        "by_outcome": dict(by_outcome),
        "by_type": {k: dict(v) for k, v in sorted(by_type.items())},
        "critical_failures": [f"{r.case_id}/r{r.repetition} {r.check_id}: {r.detail}"
                              for r in results if r.critical and r.outcome is AssertionOutcome.FAILED],
        "failures": [f"{r.case_id}/r{r.repetition} {r.check_id}: {r.detail}"
                     for r in results if not r.critical and r.outcome is AssertionOutcome.FAILED],
    }
