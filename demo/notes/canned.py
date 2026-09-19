"""Scripted replies for the six fixed questions: an offline, key-free demonstration.

THESE ARE NOT MODEL OUTPUT. They are written by hand to show the wiring end to end
(scripts/notes.py validate, no --live): retrieval, draft, the ledger, the gate sending work
back on q1, an honest gap on q5 and q6. Any report made from them says "stub" in its mode
and must never be presented as evidence about a model.

Passages come from the real corpus, split by the kit's own splitter, so a scripted draft
cites the same kind of ids a live run would (chunk ids are made here, not by embeddings).
"""
from __future__ import annotations

import json

from slice.retrieve import Chunk, split

from .corpus import note_texts
from .questions import Question, by_id
from .stub import ScriptedModel

PASS = json.dumps({"status": "PASS", "objections": []})


def _chunks(doc: str) -> list[Chunk]:
    return [Chunk(f"{doc}:{i}", doc, i, text, 0.1 * i) for i, text in enumerate(split(note_texts()[doc]))]


def _find(doc: str, needle: str) -> Chunk:
    return next(c for c in _chunks(doc) if needle in c.text)


def _answer(chunk: Chunk, text: str, requirement: str) -> str:
    return json.dumps({
        "action": "answer",
        "requirements": [{"requirement_id": "r1", "description": requirement}],
        "assessments": [{"requirement_id": "r1", "status": "supported", "evidence_id": chunk.chunk_id}],
        "text": text, "cited_evidence_ids": [chunk.chunk_id]})


def _gap(text: str, requirement: str, missing: str) -> str:
    return json.dumps({
        "action": "state_gap",
        "requirements": [{"requirement_id": "r1", "description": requirement}],
        "assessments": [{"requirement_id": "r1", "status": "missing", "gap": missing}],
        "text": text, "cited_evidence_ids": []})


def _block(problem: str) -> str:
    return json.dumps({"status": "BLOCK", "objections": [{"requirement_id": "r1", "problem": problem}]})


def scripted(question: Question) -> tuple[ScriptedModel, callable]:
    """(stub model, stub search) for one fixed question."""
    if question.id == "q1-resistance-from-table":
        table = _find("ohms-law-notes.md", "Voltage (V)")
        model = ScriptedModel({
            # A wrong first draft, so the run visibly goes backwards.
            "draft": [_answer(table, "The resistance is 3 ohms.", "resistance at 6 V and 3 A"),
                      _answer(table, "The resistance is 2 ohms: 6 V divided by 3 A is 2 ohms.",
                              "resistance at 6 V and 3 A")],
            "gate": [_block('"The resistance is 3 ohms" does not match the table: the row 6 V, 3 A '
                            "gives V / I = 2 ohms."), PASS]})
        return model, _search("ohms-law-notes.md")
    if question.id == "q2-series-current":
        worked = _find("series-circuits-notes.md", "Worked example")
        model = ScriptedModel({
            "draft": [_answer(worked, "The current is 2 A: the total resistance is 2 + 3 = 5 ohms, "
                                      "so 10 V / 5 ohms = 2 A.", "current in the series circuit")],
            "gate": [PASS]})
        return model, _search("series-circuits-notes.md")
    if question.id == "q3-power":
        power = _find("electrical-power-notes.md", "20 W")
        model = ScriptedModel({
            "draft": [_answer(power, "The power is 20 W: P = I^2 x R = 2 x 2 x 5 = 20 W.",
                              "power in the 5 ohm resistor")],
            "gate": [PASS]})
        return model, _search("electrical-power-notes.md")
    if question.id == "q4-misconception":
        ohms = _find("ohms-law-notes.md", "doubles the current")
        model = ScriptedModel({
            "draft": [_answer(ohms, "No. Doubling the voltage doubles the current, and the resistance "
                                    "stays at 2 ohms.", "effect of doubling the voltage on resistance")],
            "gate": [PASS]})
        return model, _search("ohms-law-notes.md")
    if question.id == "q5-not-covered-parallel":
        model = ScriptedModel({
            "draft": [_gap("Your notes cover series circuits only, so I cannot say what happens in "
                           "parallel.", "total current for two resistors in parallel",
                           "the notes do not cover parallel circuits")],
            "gate": [PASS]})
        return model, _search("series-circuits-notes.md")
    if question.id == "q6-not-covered-transistor":
        model = ScriptedModel({
            "draft": [_gap("Your notes do not cover transistors, so I cannot answer that.",
                           "how a transistor amplifies", "the notes do not mention transistors")],
            "gate": [PASS]})
        return model, _search("electrical-power-notes.md")
    raise KeyError(f"no scripted replies for {question.id!r}: run with --live to use a real model")


def _search(doc: str):
    chunks = _chunks(doc)
    return lambda store, query, k: chunks[:k]


def scripted_by_id(question_id: str) -> tuple[ScriptedModel, callable]:
    return scripted(by_id(question_id))


# question -> (note, text that finds the passage the notes answer cited, the Tutor turn's parts)
_TUTOR = {
    "q1-resistance-from-table": (
        "ohms-law-notes.md", "Voltage (V)",
        "The table shows that dividing each voltage by its current gives the same number every "
        "time, which is why the resistance stays the same.",
        {"prompt": "What is a resistor called if its resistance stays constant?",
         "kind": "multiple_choice", "correct_answer": "opt-1", "accepted_answers": [],
         "options": [{"option_id": "opt-1", "text": "ohmic"}, {"option_id": "opt-2", "text": "reactive"},
                     {"option_id": "opt-3", "text": "inductive"}]}),
    "q4-misconception": (
        "ohms-law-notes.md", "doubles the current",
        "Voltage pushes current through a resistor, and the resistance is a fixed property of it, "
        "so changing the voltage changes the current instead.",
        {"prompt": "What is a resistor called if its resistance stays constant?",
         "kind": "short_answer", "correct_answer": "ohmic", "accepted_answers": []}),
    "q2-series-current": (
        "series-circuits-notes.md", "Worked example",
        "The notes add the two resistances into one total, then divide the supply voltage by "
        "that total to find the current.",
        {"prompt": "What is the total resistance of the 2 ohm and 3 ohm resistors in series?",
         "kind": "short_answer", "correct_answer": "5 ohms", "accepted_answers": ["5 ohm"]}),
    "q3-power": (
        "electrical-power-notes.md", "20 W",
        "The notes use the current squared times the resistance, which gives the rate at which "
        "the resistor turns electrical energy into heat.",
        {"prompt": "In the notes, one watt is how much energy per unit of time?",
         "kind": "short_answer", "correct_answer": "one joule of energy per second",
         "accepted_answers": ["1 joule per second"]}),
}


def scripted_tutor(question_id: str) -> ScriptedModel:
    """A scripted Tutor turn for a run that ended in an answer (q1 to q4). The check question's
    answer is verbatim in the passage the notes answer cited, as the Tutor's rules require."""
    doc, needle, explanation, check = _TUTOR[question_id]
    chunk = _find(doc, needle)
    return ScriptedModel({"tutor": [json.dumps({
        "explanation": explanation, "cited_evidence_ids": [chunk.chunk_id],
        "check": {"options": [], **check, "evidence_ids": [chunk.chunk_id]}})]})
