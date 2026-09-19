"""Human-review worksheet, blank review template, and applying completed reviews.

The worksheet shows everything a reviewer needs for each case: input and
context, verbatim evidence with its source file, what is withheld, the draft
reference with its rationale, acceptable alternatives and open ambiguity, the
checks already run, the deterministic assertions and the criteria. The
reviewer fields are always blank here: this module never fills a reviewer,
decision or date.

apply_review() turns a completed template into a NEW dataset version:

- approve: the reference becomes human_gold only with a named reviewer who is
  not its author and who confirms the evidence was checked against the source;
- revise: the reviewer's corrected text replaces the reference, authored by
  that reviewer, and stays suggested until a second person approves it;
- reject: the case is marked rejected (kept for the record, not deleted).

Nothing is frozen here; freezing held-out cases stays a separate, gated step
(eval_dataset.freeze_heldout).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from case_assertions import AssertionOutcome, evaluate_case
from dataset_checks import CATEGORIES, load_rubric
from eval_dataset import DatasetFile, DatasetSnapshot, JudgeCase, LabelStatus, ReferenceLabel
from eval_store import FrozenOutput
from grounding import Registry, compute

FIRST_BATCH = [
    # Minimum set (10): unlocks reference-based judging of calibration cases
    # (the plan's floor is ten human-labelled cases per criterion).
    "pack-01-full-evidence",
    "pack-02-transcript-only",
    "pack-07-which-object-gives-2-ohms",
    "pack-09-copied-current-value",
    "pack-10-hint-after-reasoning",
    "pack-11-assisted-correct-answer",
    "lamp-01-is-it-ohmic",
    "lamp-02-always-2-ohms",
    "m1-01-injection-in-retrieved-page",
    "pack-05-unreadable-axes",
    # Rest of the first batch: remaining calibration cases, then one ordinary
    # and one access-control development case.
    "pack-03-no-evidence-at-this-time",
    "pack-04-video-analysis-failed",
    "pack-06-equation-failed-check",
    "pack-08-offer-optional-check",
    "pack-12-declined-check",
    "m1-02-student-asks-about-page-4",
    "ch4-13-resistance-from-table",
    "ch4-20-other-students-note",
]
MINIMUM_BATCH = FIRST_BATCH[:10]


def _assertion_text(assertion) -> str:
    kind = assertion.type
    detail = {
        "cites_only_supplied": "cites only supplied evidence",
        "must_cite_any": f"cites at least one of {getattr(assertion, 'evidence_ids', None)}",
        "must_not_cite": f"never cites {getattr(assertion, 'evidence_ids', None)}",
        "states_quantity": f"states {getattr(assertion, 'value', 0):g} {getattr(assertion, 'unit', '')}",
        "must_not_state_quantity": f"does not state {getattr(assertion, 'value', 0):g} {getattr(assertion, 'unit', '')}",
        "must_not_contain": f"contains none of {getattr(assertion, 'phrases', None)}",
        "pending_question": f"leaves a question pending: {getattr(assertion, 'expected', None)}",
        "no_learning_event": "proposes no learning event or grade",
    }.get(kind, kind)
    flag = " **(critical)**" if assertion.critical else ""
    reason = f" — {assertion.reason}" if assertion.reason else ""
    return f"{detail}{flag}{reason}"


def _calc_text(calc) -> str:
    value, unit = compute(calc)
    parts = " , ".join(f"{q.value:g} {q.unit}" + (f" [{q.evidence_id}]" if q.evidence_id else " [student's words]")
                       for q in calc.operands)
    return f"`{calc.calc_id}` {calc.operation}({parts}) = {float(value):g} {unit} (recomputed; expected {calc.expected.value:g} {calc.expected.unit})"


def _reference_self_check(case: JudgeCase) -> str:
    if case.reference is None:
        return "no reference"
    probe = FrozenOutput(run_id="review", case_id=case.case_id, repetition=0, response=case.reference.text,
                         response_hash="n/a", generated_at="2026-09-19T00:00:00Z", cited_evidence_ids=None,
                         structured=None, origin="fixture")
    results = [r for r in evaluate_case(case, probe) if r.outcome is not AssertionOutcome.NOT_EVALUABLE]
    failed = [r for r in results if r.outcome is AssertionOutcome.FAILED]
    return (f"the draft reference passes its {len(results)} text assertion(s)" if not failed
            else f"the draft reference FAILS: {[r.detail for r in failed]}")


def render_worksheet(snapshot: DatasetSnapshot, registry: Registry, batch: list[str]) -> str:
    order = batch + [case.case_id for case in snapshot.cases if case.case_id not in batch]
    lines = [
        f"# Review worksheet: {snapshot.snapshot_id}",
        "",
        "Every reference below is an **unreviewed draft** (status `suggested`). Nothing here is gold, calibrated or a "
        "claim about student learning. All sources are synthetic: project fixtures that already existed in the repository, "
        "or clearly labelled miniatures written for evaluation (`evaluation/sources/`). The M3 chart, table and equation "
        "texts are renderings of structured fixture objects, not quotations from a real PDF.",
        "",
        "Record decisions in `review_template.json` (one entry per case), then apply them with "
        "`eval_cli.py apply-review`. A reference becomes gold only when approved by a named reviewer who is not its "
        "author, with the evidence checked against the listed source file. A revised reference needs a second reviewer.",
        "",
        f"**First review batch ({len(batch)} cases).** The first {len(MINIMUM_BATCH)} are the minimum that unlocks "
        "reference-based judging of calibration cases (the plan's floor is ten human-labelled cases per criterion). "
        "Held-out candidates are listed last; reviewing them is allowed, but do not use them to tune prompts, rubrics "
        "or implementation.",
        "",
    ]
    for number, case_id in enumerate(order, start=1):
        case = snapshot.case(case_id)
        batch_label = ("minimum batch" if case_id in MINIMUM_BATCH else "first batch" if case_id in batch
                       else "later")
        lines += [
            f"## {number}. `{case.case_id}` ({batch_label})",
            "",
            f"- Proposed split: **{case.split}** (not frozen); family `{case.problem_family}`; category "
            f"`{case.category}` ({CATEGORIES.get(case.category or '', '?')}); kind `{case.kind}`; expected behaviour "
            f"`{case.expected_behavior}`.",
            f"- Provenance: {case.provenance.origin if case.provenance else '?'}; source files: "
            f"{', '.join(f'`{f}`' for f in (case.provenance.source_files if case.provenance else []))}"
            + (f"; derived from {', '.join(f'`{d}`' for d in case.provenance.derived_from)}" if case.provenance and case.provenance.derived_from else ""),
            f"- Prior exposure: {'**yes**, not eligible as untouched held-out: ' + '; '.join(case.prior_exposure) if case.prior_exposure else 'none recorded'}",
            "",
            f"**Student input:** “{case.student_input}”",
        ]
        context = case.session_context
        if context:
            facts = []
            if context.pinned_source_version_ids:
                facts.append("pinned " + ", ".join(f"`{v}`" for v in context.pinned_source_version_ids))
            if context.interaction_mode:
                facts.append(f"mode {context.interaction_mode}")
            if context.player_time_ms is not None:
                facts.append(f"lecture paused at {context.player_time_ms // 1000} s")
            if context.pending_question:
                facts.append(f"pending question “{context.pending_question}”")
            if context.notes:
                facts.append(context.notes)
            lines.append(f"**Session:** {'; '.join(facts)}")
        if case.conversation:
            lines += ["", "**Earlier turns:**", ""]
            lines += [f"{i}. {turn.role}: “{turn.content}”" + (" *(quoted from the AgentSpec walkthrough)*"
                                                                  if turn.note and turn.note.startswith("quote:") else "")
                      for i, turn in enumerate(case.conversation, start=1)]
        lines += ["", "**Evidence supplied to Netra (verbatim):**", ""]
        if case.source_excerpts:
            lines += ["| evidence | source · locator | trust | text |", "|---|---|---|---|"]
            for item in case.source_excerpts:
                where = f"{item.source_key} · {item.locator}"
                if item.start_ms is not None:
                    where += f" · {item.start_ms // 1000}–{item.end_ms // 1000} s"  # type: ignore[operator]
                if item.variant:
                    where += f" · variant {item.variant}"
                text = item.text.replace("|", "\\|")
                lines.append(f"| `{item.evidence_id}` | {where} | {item.trust} | {text} |")
        else:
            lines.append("_None: nothing relevant was retrieved._")
        if case.withheld_evidence:
            lines += ["", "**Withheld (exists in the scenario, must never reach the answer; text not shown):**", ""]
            lines += [f"- `{held.evidence_id}` from `{held.source_key}`: {held.reason}" for held in case.withheld_evidence]
        reference = case.reference
        lines += ["", "**Draft reference (score-5 example response):**", ""]
        if reference is None:
            lines.append("_None on purpose: reference-based criteria must come out missing/reference_pending._")
        else:
            lines += [f"> {reference.text}", "",
                      f"- Status: `{reference.status.value}`; author: {reference.author}; reviewer: none",
                      f"- Why this behaviour: {reference.rationale or '(not recorded)'}"]
            for alternative in reference.acceptable_alternatives:
                lines.append(f"- Also acceptable: {alternative}")
            if reference.ambiguity:
                lines.append(f"- **Ambiguity to resolve:** {reference.ambiguity}")
        checks = [_reference_self_check(case), "every excerpt matches the source registry verbatim (grounding.py)"]
        checks += [_calc_text(calc) for calc in case.calculations]
        lines += ["", "**Checks already run (deterministic):**", ""] + [f"- {check}" for check in checks]
        lines += ["", "**Deterministic assertions applied to candidate outputs:**", ""]
        lines += [f"- {_assertion_text(assertion)}" for assertion in case.assertions]
        criteria = []
        for criterion in case.criteria:
            rubric = load_rubric(criterion)
            criteria.append(f"`{criterion}`" + (f" ({rubric.status})" if rubric else " (missing rubric!)"))
        lines += ["", f"**Judge criteria:** {', '.join(criteria)}"]
        if case.limitations:
            lines += ["", "**Limitations:**", ""] + [f"- {item}" for item in case.limitations]
        lines += [
            "", "**Reviewer (fill in `review_template.json`):**", "",
            "- [ ] Evidence checked against the source file(s) above",
            "- Decision: approve / revise / reject",
            "- Corrected reference (if revising):",
            "- Criteria or assertion changes:",
            "- Reviewer name: ______  Date: ______",
            "",
        ]
    return "\n".join(lines) + "\n"


class ReviewEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    decision: Optional[Literal["approve", "revise", "reject"]] = None
    corrected_reference: Optional[str] = None
    source_checked: bool = False
    reviewer: Optional[str] = None
    reviewed_on: Optional[str] = None
    criteria_changes: Optional[str] = None
    notes: Optional[str] = None


class ReviewFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset: str
    instructions: str
    entries: list[ReviewEntry] = Field(default_factory=list)


def review_template(snapshot: DatasetSnapshot, batch: list[str]) -> ReviewFile:
    order = batch + [case.case_id for case in snapshot.cases if case.case_id not in batch]
    return ReviewFile(
        dataset=snapshot.snapshot_id,
        instructions=("Fill decision (approve/revise/reject), source_checked, reviewer and reviewed_on for each case you "
                      "review; leave the rest null. approve needs source_checked=true and a reviewer who is not the "
                      "reference author. revise needs corrected_reference; a second reviewer must later approve it."),
        entries=[ReviewEntry(case_id=case_id) for case_id in order],
    )


class ReviewRejectedError(Exception):
    pass


def apply_review(dataset: DatasetFile, snapshot: DatasetSnapshot, review: ReviewFile, new_name: str) -> DatasetFile:
    if review.dataset != snapshot.snapshot_id:
        raise ReviewRejectedError(f"review is for {review.dataset}, not {snapshot.snapshot_id}")
    if new_name == dataset.dataset_name:
        raise ReviewRejectedError("a reviewed dataset is a new version: choose a new dataset name")
    by_id = {case.case_id: case for case in dataset.cases}
    updated: dict[str, JudgeCase] = {}
    for entry in review.entries:
        if entry.decision is None:
            continue
        case = by_id.get(entry.case_id)
        if case is None:
            raise ReviewRejectedError(f"unknown case {entry.case_id}")
        if not entry.reviewer or not entry.reviewer.strip() or not entry.reviewed_on:
            raise ReviewRejectedError(f"{entry.case_id}: a decision needs a named reviewer and a date")
        if entry.decision == "reject":
            updated[case.case_id] = case.model_copy(update={"review_status": "rejected"})
            continue
        if entry.decision == "approve":
            if case.reference is None:
                raise ReviewRejectedError(f"{entry.case_id}: there is no reference to approve")
            if not entry.source_checked:
                raise ReviewRejectedError(f"{entry.case_id}: approval requires checking the evidence against the source")
            gold = ReferenceLabel.model_validate({**case.reference.model_dump(), "status": LabelStatus.HUMAN_GOLD,
                                                  "reviewer": entry.reviewer, "source_checked": True})
            updated[case.case_id] = case.model_copy(update={"reference": gold, "review_status": "approved"})
            continue
        if not entry.corrected_reference:
            raise ReviewRejectedError(f"{entry.case_id}: revise needs corrected_reference")
        base: dict[str, Any] = case.reference.model_dump() if case.reference else {}
        revised = ReferenceLabel.model_validate({**base, "text": entry.corrected_reference,
                                                 "status": LabelStatus.SUGGESTED, "author": entry.reviewer,
                                                 "reviewer": None, "source_checked": entry.source_checked})
        updated[case.case_id] = case.model_copy(update={"reference": revised, "review_status": "needs_revision"})
    cases = [updated.get(case.case_id, case) for case in dataset.cases]
    return DatasetFile(dataset_name=new_name, description=dataset.description, cases=cases,
                       status=f"reviewed from {snapshot.snapshot_id}; see the review file for decisions")


def write_review_package(snapshot: DatasetSnapshot, registry: Registry, out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    worksheet = out_dir / "worksheet.md"
    template = out_dir / "review_template.json"
    worksheet.write_text(render_worksheet(snapshot, registry, FIRST_BATCH), encoding="utf-8")
    template.write_text(json.dumps(review_template(snapshot, FIRST_BATCH).model_dump(mode="json"), indent=2,
                                   ensure_ascii=False) + "\n", encoding="utf-8")
    return {"worksheet": str(worksheet), "template": str(template)}
