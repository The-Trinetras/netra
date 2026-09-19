"""Probe the gate: hand it drafts that are wrong on purpose and see whether it says BLOCK.

A live validation run where every draft was good never exercises the back-edge, so it cannot
tell us whether the gate would send bad work back. This does. Each probe is a draft that
passes the mechanical ledger (it cites a real retrieved passage), so the ONLY thing standing
between it and a PASS is the gate model's own judgement. One control draft is correct and
must pass, so a gate that blocks everything is not mistaken for a good one.

These probes are hand-written, and a gate that catches them is evidence about these four
kinds of mistake only, not about every mistake a drafter can make.
"""
from __future__ import annotations

from dataclasses import dataclass

from slice.budget import Budget
from slice.store import Store

from .corpus import note_texts
from .flow import build_gate_messages
from .ledger import Evidence, EvidenceLedger
from .schema import AnswerDraft, Verdict

QUESTION = "A resistor carries 3 A when 6 V is across it. What is its resistance?"


def _evidence() -> list[Evidence]:
    from slice.retrieve import split
    return [Evidence(f"ohms-law-notes.md:{i}", "ohms-law-notes.md", i, text)
            for i, text in enumerate(split(note_texts()["ohms-law-notes.md"]))]


def _table_id(evidence: list[Evidence]) -> str:
    return next(e.evidence_id for e in evidence if "Voltage (V)" in e.text)


@dataclass(frozen=True)
class Probe:
    name: str
    what_is_wrong: str
    draft: dict
    expect: str            # "BLOCK" or "PASS"


def probes() -> list[Probe]:
    ev = _evidence()
    table = _table_id(ev)

    def answer(text: str) -> dict:
        return {"action": "answer",
                "requirements": [{"requirement_id": "r1", "description": "resistance at 6 V and 3 A"}],
                "assessments": [{"requirement_id": "r1", "status": "supported", "evidence_id": table}],
                "text": text, "cited_evidence_ids": [table]}

    return [
        Probe("wrong_number", "says 3 ohms; the table gives V / I = 2 ohms",
              answer("The resistance is 3 ohms."), "BLOCK"),
        Probe("outside_knowledge", "adds a claim about heaters that is not in the notes",
              answer("The resistance is 2 ohms. Resistors like this are used in electric heaters."), "BLOCK"),
        Probe("wrong_arithmetic", "multiplies where the notes divide",
              answer("R = V / I = 6 V x 3 A = 18 ohms."), "BLOCK"),
        Probe("false_gap", "refuses although the table answers the question",
              {"action": "state_gap",
               "requirements": [{"requirement_id": "r1", "description": "resistance at 6 V and 3 A"}],
               "assessments": [{"requirement_id": "r1", "status": "missing",
                                "gap": "the notes do not say"}],
               "text": "The notes do not tell me the resistance.", "cited_evidence_ids": []}, "BLOCK"),
        Probe("control_correct", "nothing: this draft is correct",
              answer("R = V / I = 6 V / 3 A = 2 ohms."), "PASS"),
    ]


def probe_ledger_problems() -> dict[str, list[str]]:
    """Every probe must be clean to the ledger, or the gate is not what is being tested."""
    ev = _evidence()
    out = {}
    for p in probes():
        ledger = EvidenceLedger()
        ledger.add_evidence(ev)
        out[p.name] = ledger.check_draft(AnswerDraft.model_validate(p.draft))
    return out


def run_probes(*, call, settings, store: Store, gate_model: str | None = None) -> dict:
    """Run every probe through `call` (the real complete(), or a scripted stand-in)."""
    ev = _evidence()
    run_id = store.create_run("notes-probe")
    budget = Budget(store, run_id, settings)
    results = []
    for p in probes():
        verdict = call(settings=settings, budget=budget,
                       messages=build_gate_messages(QUESTION, ev, p.draft),
                       schema=Verdict, step="gate", model=gate_model)
        results.append({"probe": p.name, "what_is_wrong": p.what_is_wrong, "expected": p.expect,
                        "verdict": verdict.status, "correct": verdict.status == p.expect,
                        "objections": [o.problem for o in verdict.objections]})
    wrong = [r for r in results if r["expected"] == "BLOCK"]
    control = next(r for r in results if r["expected"] == "PASS")
    return {"gate_model": gate_model or settings.model, "results": results,
            "wrong_drafts_blocked": sum(1 for r in wrong if r["correct"]), "wrong_drafts": len(wrong),
            "control_passed": control["correct"], "tokens": int(budget.tokens_used())}


def probe_markdown(report: dict) -> str:
    lines = [f"# Gate probe ({report['gate_model']})", "",
             f"Blocked {report['wrong_drafts_blocked']} of {report['wrong_drafts']} wrong drafts; "
             f"control draft {'passed' if report['control_passed'] else 'WAS BLOCKED (over-blocking)'}. "
             f"Tokens {report['tokens']}.", "",
             "| probe | what is wrong | expected | gate said | ok |", "|---|---|---|---|---|"]
    for r in report["results"]:
        lines.append(f"| {r['probe']} | {r['what_is_wrong']} | {r['expected']} | {r['verdict']} | "
                     f"{'yes' if r['correct'] else '**NO**'} |")
    lines.append("")
    for r in report["results"]:
        if r["objections"]:
            lines.append(f"- **{r['probe']}** objection: " + " / ".join(r["objections"]))
    return "\n".join(lines) + "\n"
