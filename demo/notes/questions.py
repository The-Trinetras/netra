"""The fixed question set: the same cases for the stub, for OpenRouter and for any
other provider used for stress testing.

Each question says what a correct outcome is, and tests/test_notes_corpus.py
checks that against the corpus itself - the expected facts really are in the
notes, and the "not covered" topics really are absent. Change the corpus or a
question and that test tells you if they have drifted apart.

`outcome`:
  "answer" - the notes support an answer; the agent must give it and cite it.
  "gap"    - the notes do not support an answer; the agent must say so, not guess.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Question:
    id: str
    text: str
    outcome: Literal["answer", "gap"]
    facts: tuple[str, ...] = ()          # strings a correct answer must contain
    evidence: tuple[str, ...] = ()       # corpus files a correct answer must cite
    absent: tuple[str, ...] = ()         # terms the corpus must NOT contain (gap questions)
    why: str = ""


QUESTIONS: tuple[Question, ...] = (
    Question(
        id="q1-resistance-from-table",
        text="A resistor carries 3 A when 6 V is across it. What is its resistance?",
        outcome="answer",
        facts=("2 ohm",),
        evidence=("ohms-law-notes.md",),
        why="Directly in the Ohm's law table: 6 V, 3 A, V / I = 2 ohms.",
    ),
    Question(
        id="q2-series-current",
        text="A 2 ohm and a 3 ohm resistor are in series across 10 V. What current flows?",
        outcome="answer",
        facts=("2 A",),
        evidence=("series-circuits-notes.md",),
        why="Worked example: total 5 ohms, so 10 / 5 = 2 A.",
    ),
    Question(
        id="q3-power",
        text="How much power does a 5 ohm resistor use when 2 A flows through it?",
        outcome="answer",
        facts=("20 W",),
        evidence=("electrical-power-notes.md",),
        why="Worked example: P = I^2 x R = 2 x 2 x 5 = 20 W.",
    ),
    Question(
        id="q4-misconception",
        text="If I double the voltage across a resistor, does its resistance double?",
        outcome="answer",
        facts=("current",),
        evidence=("ohms-law-notes.md",),
        why="The 'common mistake' section: the current doubles, the resistance stays the same.",
    ),
    Question(
        id="q5-not-covered-parallel",
        text="Two resistors of 2 ohm and 3 ohm are in parallel across 10 V. What is the total current?",
        outcome="gap",
        absent=("parallel",),
        why="The notes cover series only. The agent must state the gap, not use outside knowledge.",
    ),
    Question(
        id="q6-not-covered-transistor",
        text="How does a transistor amplify a signal?",
        outcome="gap",
        absent=("transistor",),
        why="Not in the notes at all.",
    ),
)


def by_id(question_id: str) -> Question:
    return next(q for q in QUESTIONS if q.id == question_id)
