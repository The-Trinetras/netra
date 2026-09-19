"""Evaluate finished notes runs against the fixed question set, and compare two reports.

Ported from the earlier project's evaluation package, scaled to this slice. What is kept:

  * Deterministic assertions per case. A case is "passed", "failed" or "not_evaluable"
    (the run never finished). A failure is never softened, and a missing result is never
    counted as a pass.
  * The run records how it was made (models, fallback, prompt and corpus hashes), so a
    number can be traced to a configuration.
  * A paired comparison of two reports: only like-for-like cases are compared, and a
    regression is shown, not averaged away.

What is NOT ported, on purpose: the Modal-hosted Prometheus judge, calibration against
human labels, the Arize AX upload, the review sheets, and the 59-case dataset (which is
written for the earlier project's sources, not these notes). So every check here is a
mechanical one; there is no quality score, and nothing here claims one.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from slice.records import RunState

from .corpus import note_texts
from .ledger import Evidence, EvidenceLedger
from .questions import QUESTIONS, Question
from .schema import AnswerDraft

Status = Literal["passed", "failed", "not_evaluable"]
_PROMPTS = Path(__file__).parent / "prompts"


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class CaseResult:
    question_id: str
    expected: str
    status: Status
    final_state: str
    checks: list[Check] = field(default_factory=list)
    drafts: int = 0
    blocks: int = 0
    model_calls: int = 0
    tokens: int = 0
    stop_kind: str | None = None
    answer_text: str = ""


# ---------------------------------------------------------------- assertions

def _has(text: str, fact: str) -> bool:
    """The fact appears at a word start, so '2 ohm' is found in '2 ohms' but '2 A' is not
    found inside '12 A'."""
    return re.search(rf"(?<!\w){re.escape(fact.casefold())}", text.casefold()) is not None


def evaluate_run(store, run_id: str, question: Question, tokens: int = 0) -> CaseResult:
    """Judge one finished run against what its question expects. Deterministic."""
    state = store.get_state(run_id)
    drafts = store.history(run_id, "draft")
    verdicts = store.history(run_id, "verdict")
    result = CaseResult(
        question_id=question.id, expected=question.outcome, status="failed", final_state=state.value,
        drafts=len(drafts), blocks=sum(1 for v in verdicts if v.payload["status"] == "BLOCK"),
        model_calls=len(drafts) + sum(1 for v in verdicts if v.produced_by == "agent:gate"),
        tokens=tokens)

    if not state.is_terminal:
        result.status = "not_evaluable"
        result.checks.append(Check("run_finished", False, f"the run ended in state {state.value}"))
        return result

    failure = store.latest(run_id, "failure")
    if state is RunState.FAILED:
        result.stop_kind = (failure or {}).get("kind")
        result.answer_text = (failure or {}).get("reply", "")
        result.checks.append(Check("run_completed", False, f"stopped: {result.stop_kind}"))
        return result
    result.checks.append(Check("run_completed", True))

    draft = AnswerDraft.model_validate(store.latest(run_id, "draft"))
    result.answer_text = draft.text
    result.checks.append(Check("gate_passed", verdicts[-1].payload["status"] == "PASS"))

    passages = store.latest(run_id, "evidence")["passages"]
    ledger = EvidenceLedger()
    ledger.add_evidence(Evidence(**p) for p in passages)
    problems = ledger.check_draft(draft)
    result.checks.append(Check("ledger_clean", not problems, ", ".join(problems)))

    if question.outcome == "answer":
        result.checks.append(Check("gave_an_answer", draft.action == "answer", f"action was {draft.action}"))
        for fact in question.facts:
            result.checks.append(Check(f"states_{fact}", _has(draft.text, fact)))
        cited_docs = {p["doc"] for p in passages if p["evidence_id"] in draft.cited_evidence_ids}
        for doc in question.evidence:
            result.checks.append(Check(f"cites_{doc}", doc in cited_docs,
                                       f"cited: {sorted(cited_docs) or 'nothing'}"))
    else:
        result.checks.append(Check("stated_a_gap", draft.action == "state_gap",
                                   f"action was {draft.action}: the notes do not cover this"))

    result.status = "passed" if all(c.passed for c in result.checks) else "failed"
    return result


# ------------------------------------------------------------------- reports

def _sha(*parts: str) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode("utf-8"))
    return h.hexdigest()[:16]


def fingerprint() -> dict:
    """What the cases were run against. Two reports are only comparable if these match."""
    prompts = "".join((_PROMPTS / f"{n}.md").read_text(encoding="utf-8") for n in ("draft", "gate", "tutor"))
    return {
        "corpus_sha256": _sha(*(f"{k}\n{v}" for k, v in sorted(note_texts().items()))),
        "questions_sha256": _sha(*(json.dumps(asdict(q), sort_keys=True) for q in QUESTIONS)),
        "prompts_sha256": _sha(prompts),
    }


def build_report(label: str, mode: str, config: dict, results: list[CaseResult]) -> dict:
    counts = {s: sum(1 for r in results if r.status == s) for s in ("passed", "failed", "not_evaluable")}
    return {
        "label": label, "mode": mode, "config": config, "fingerprint": fingerprint(),
        "summary": {**counts, "total": len(results),
                    "model_calls": sum(r.model_calls for r in results),
                    "tokens": sum(r.tokens for r in results)},
        "cases": [asdict(r) for r in results],
    }


def report_markdown(report: dict) -> str:
    s = report["summary"]
    lines = [
        f"# Notes validation: {report['label']}",
        "",
        f"Mode: **{report['mode']}**. Passed {s['passed']} / {s['total']}, failed {s['failed']}, "
        f"not evaluable {s['not_evaluable']}. Model calls {s['model_calls']}, tokens {s['tokens']}.",
        "",
        "Config: " + ", ".join(f"{k}={v}" for k, v in report["config"].items()),
        "",
        "| question | expected | status | drafts | blocks | calls | failed checks |",
        "|---|---|---|---|---|---|---|",
    ]
    for c in report["cases"]:
        failed = "; ".join(f"{k['name']} ({k['detail']})" if k["detail"] else k["name"]
                           for k in c["checks"] if not k["passed"]) or "-"
        lines.append(f"| {c['question_id']} | {c['expected']} | {c['status']} | {c['drafts']} | "
                     f"{c['blocks']} | {c['model_calls']} | {failed} |")
    if report["mode"] != "live":
        lines += ["", "**This was a scripted (stub) run.** It proves the wiring, not model behavior."]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- comparison

def compare_reports(baseline: dict, candidate: dict) -> dict:
    """Paired comparison. Regressions are listed, never netted against improvements."""
    notes = []
    for key in ("corpus_sha256", "questions_sha256", "prompts_sha256"):
        if baseline["fingerprint"][key] != candidate["fingerprint"][key]:
            notes.append(f"{key} differs: the two runs did not see the same "
                         f"{key.split('_')[0]}, so differences may come from that.")
    if baseline["mode"] != candidate["mode"]:
        notes.append(f"mode differs ({baseline['mode']} vs {candidate['mode']}): a stub run and a "
                     "live run are not comparable.")

    base = {c["question_id"]: c for c in baseline["cases"]}
    cand = {c["question_id"]: c for c in candidate["cases"]}
    paired = sorted(set(base) & set(cand))
    regressions, improvements, unchanged = [], [], []
    for qid in paired:
        before, after = base[qid]["status"], cand[qid]["status"]
        if before == after:
            unchanged.append(qid)
        elif before == "passed":
            regressions.append({"question_id": qid, "before": before, "after": after})
        else:
            improvements.append({"question_id": qid, "before": before, "after": after})
    return {
        "paired_cases": len(paired),
        "only_in_baseline": sorted(set(base) - set(cand)),
        "only_in_candidate": sorted(set(cand) - set(base)),
        "regressions": regressions, "improvements": improvements, "unchanged": unchanged,
        "model_calls": {"baseline": baseline["summary"]["model_calls"], "candidate": candidate["summary"]["model_calls"]},
        "tokens": {"baseline": baseline["summary"]["tokens"], "candidate": candidate["summary"]["tokens"]},
        "notes": notes,
    }


def comparison_markdown(cmp: dict, baseline: str, candidate: str) -> str:
    lines = [f"# Comparison: {baseline} -> {candidate}", "",
             f"Paired cases: {cmp['paired_cases']}. Regressions: {len(cmp['regressions'])}. "
             f"Improvements: {len(cmp['improvements'])}. Unchanged: {len(cmp['unchanged'])}.", ""]
    for n in cmp["notes"]:
        lines.append(f"- WARNING: {n}")
    for r in cmp["regressions"]:
        lines.append(f"- REGRESSION {r['question_id']}: {r['before']} -> {r['after']}")
    for r in cmp["improvements"]:
        lines.append(f"- improved {r['question_id']}: {r['before']} -> {r['after']}")
    lines.append(f"- model calls: {cmp['model_calls']['baseline']} -> {cmp['model_calls']['candidate']}; "
                 f"tokens: {cmp['tokens']['baseline']} -> {cmp['tokens']['candidate']}")
    return "\n".join(lines) + "\n"
