"""Dataset-level rules for source-grounded evaluation datasets.

grounding.py checks each case against its sources; this module checks the
dataset as a whole, so comparisons stay valid:

- Split hygiene: a problem family never spans splits (near-duplicates cannot
  leak between development, calibration and held-out), and a case with prior
  exposure (it already informed implementation or tests) is never held-out.
- Judge fidelity: the judge's instruction must contain the student's actual
  input and every earlier turn, so the judge grades what the producer saw.
- Completeness: grounded cases declare category, family, input, expected
  behaviour, provenance and review status; criteria must exist as rubrics.
- Size: the judge prompt must fit the conservative 4,096-token total with
  512 reserved for generation. No tokenizer is installed here, so the size is
  an upper-bound ESTIMATE (characters / 3); the pinned tokenizer must confirm
  it before a live run, and oversized cases are shortened by review, never
  truncated.
"""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from pathlib import Path
from typing import Iterable, Optional

from eval_dataset import DatasetSnapshot, JudgeCase
from grounding import Issue
from judge_runner import instruction_for
from prometheus import AnchoredRubric, build_absolute_prompt

REPO = Path(__file__).resolve().parents[2]
RUBRICS = REPO / "evaluation" / "rubrics"

CATEGORIES: dict[str, str] = {
    "explanation_calculation": "Correct, source-supported explanation or calculation",
    "misconception_tutoring": "Misconception and tutoring guidance",
    "evidence_state": "Missing, contradictory, stale, deleted or denied evidence",
    "citation_support": "Citation validity versus actual support for a claim",
    "transcript_visual": "Transcript evidence versus claims about visual content",
    "unanswerable_uncertainty": "Unanswerable or ambiguous question and appropriate uncertainty",
    "optional_check_multiturn": "Optional-question grounding and multi-turn behaviour",
    "embedded_instruction": "Instructions embedded in retrieved material",
    "judge_robustness": "Candidate text that tries to steer the judge (scorer robustness)",
    "assessment_integrity": "Answer-key leaks and mastery labels",
}

MAX_TOTAL_TOKENS = 4096
MAX_NEW_TOKENS = 512
CHARS_PER_TOKEN_ESTIMATE = 3.0
RESPONSE_ALLOWANCE_CHARS = 1600
"""A generous candidate-response length used only for the size estimate."""


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN_ESTIMATE)


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


def load_rubric(evaluation_id: str) -> Optional[AnchoredRubric]:
    path = RUBRICS / f"{evaluation_id}.json"
    if not path.exists():
        return None
    return AnchoredRubric.model_validate(json.loads(path.read_text(encoding="utf-8")))


def is_grounded(case: JudgeCase) -> bool:
    return case.problem_family is not None


def check_dataset(snapshot: DatasetSnapshot) -> list[Issue]:
    issues: list[Issue] = []

    def add(case_id: str, code: str, detail: str, severity: str = "error") -> None:
        issues.append(Issue(case_id, code, severity, detail))  # type: ignore[arg-type]

    grounded = [case for case in snapshot.cases if is_grounded(case)]
    rubrics: dict[str, Optional[AnchoredRubric]] = {}

    families: dict[str, set[str]] = defaultdict(set)
    inputs: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for case in snapshot.cases:
        for criterion in case.criteria:
            if criterion not in rubrics:
                rubrics[criterion] = load_rubric(criterion)
            rubric = rubrics[criterion]
            if rubric is None:
                add(case.case_id, "criterion_without_rubric", f"no rubric file for {criterion}")
            elif rubric.applies_to_modes and case.kind not in rubric.applies_to_modes:
                add(case.case_id, "criterion_not_applicable_to_kind", f"{criterion} does not apply to kind {case.kind}")

    for case in grounded:
        missing = [name for name in ("category", "student_input", "expected_behavior", "provenance", "review_status")
                   if getattr(case, name) in (None, "")]
        if missing:
            add(case.case_id, "grounded_case_incomplete", f"missing {', '.join(missing)}")
        if case.category is not None and case.category not in CATEGORIES:
            add(case.case_id, "unknown_category", f"{case.category} is not a coverage category")
        families[case.problem_family].add(case.split)  # type: ignore[index]
        if case.prior_exposure and case.split == "heldout":
            add(case.case_id, "exposed_case_in_heldout", "a case that informed implementation cannot be held-out")
        if case.student_input and case.student_input not in case.instruction:
            add(case.case_id, "instruction_omits_input", "the judge instruction does not contain the student's input")
        for turn in case.conversation:
            if turn.content not in case.instruction:
                add(case.case_id, "instruction_omits_turn", f"the judge instruction omits the turn {turn.content[:40]!r}")
        if case.split in ("calibration", "heldout") and case.reference is None:
            add(case.case_id, "reference_missing_for_split", f"a {case.split} case needs a reference before use", "warning")
        if case.reference is not None and case.reference.is_gold and case.review_status != "approved":
            add(case.case_id, "gold_without_case_approval", "gold reference on a case whose design is not approved", "warning")
        if case.student_input:
            inputs[_normalize(case.student_input)].append((case.case_id, case.problem_family or ""))

        if case.reference is not None:
            instruction = instruction_for(case)
            for criterion in case.criteria:
                rubric = rubrics.get(criterion)
                if rubric is None:
                    continue
                prompt = build_absolute_prompt(rubric, instruction, "x" * RESPONSE_ALLOWANCE_CHARS, case.reference.text)
                estimate = estimate_tokens(prompt)
                budget = MAX_TOTAL_TOKENS - MAX_NEW_TOKENS
                if estimate > budget:
                    add(case.case_id, "judge_prompt_too_large_estimate",
                        f"{criterion}: about {estimate} tokens (estimate) exceeds {budget}")
                elif estimate > 0.9 * budget:
                    add(case.case_id, "judge_prompt_near_limit_estimate",
                        f"{criterion}: about {estimate} tokens (estimate), over 90% of {budget}", "warning")

    for family, splits in sorted(families.items()):
        if len(splits) > 1:
            add(f"family:{family}", "family_spans_splits", f"{family} appears in {sorted(splits)}")
    for normalized, owners in inputs.items():
        if len(owners) > 1:
            ids = ", ".join(case_id for case_id, _ in owners)
            crossing = len({family for _, family in owners}) > 1
            add(owners[0][0], "duplicate_student_input",
                f"identical student input in {ids}" + (" across families" if crossing else " (same family)"), "warning")
    return issues


def coverage(cases: Iterable[JudgeCase]) -> dict[str, dict[str, int]]:
    """category -> split -> count (grounded cases only)."""

    table: dict[str, dict[str, int]] = {category: {} for category in CATEGORIES}
    for case in cases:
        if case.category in table:
            table[case.category][case.split] = table[case.category].get(case.split, 0) + 1
    return table
