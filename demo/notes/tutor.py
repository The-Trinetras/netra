"""The Tutor: explain a checked answer, optionally ask one check question, grade the reply.

Ported from the earlier project's Tutor (learning/tutor, learning/quiz, learning/assessment),
reshaped onto the kit. It is a SECOND flow, not a step inside the notes flow: the notes run
ends COMPLETE with a gated answer, and a tutor run is started from it. So a Tutor failure
can never take a checked answer away.

It uses the kit's own states and callback, and slice/ is unchanged:

    DRAFTING          the Tutor model writes an explanation and an optional check question
    GATING            code checks that turn (a failure goes back to DRAFTING); if it has a
                      question the run suspends on the student
    AWAITING_EXPERT   (the kit's name for "suspended on a human": here, the student)
    GATING            the student's answer arrives; code grades it - no model call

Rules ported from the earlier project, enforced in code here rather than asked of the model:
  * The private answer key is a separate record, never in the student-facing view, the
    question's stored context, or any prompt. It is shown only after the answer is final.
  * The explanation and question text may not contain the answer to the check question.
  * The Tutor sees only a scoped hand-off (the question, the validated answer, the cited
    passages), never the notes run's drafts, objections or ledger.
  * Grading is deterministic. No mastery labels, no guesses about the student: an attempt
    is recorded as facts (what was answered, whether it matched).
  * Check questions are fail-closed: the correct answer must appear verbatim in the cited
    evidence. That limits questions to what the notes state in plain words, and it is a
    product choice, not a proof that a question is good. (The earlier project also left
    optional-check support unapproved until a grounding rule was decided; this is ours.)
"""
from __future__ import annotations

import html
import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from slice import callback
from slice.llm import complete
from slice.records import RunState
from slice.store import Store

from .ledger import Evidence
from .schema import AnswerDraft

MAX_TUTOR_REVISIONS = 2
"""Revisions after the first Tutor draft, so at most three model calls per tutor run."""

_PROMPTS = Path(__file__).parent / "prompts"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ------------------------------------------------------------------- records

class Option(_Strict):
    option_id: str = Field(min_length=1, max_length=20)
    text: str = Field(min_length=1, max_length=200)


class CheckQuestion(_Strict):
    prompt: str = Field(min_length=1, max_length=500)
    kind: Literal["multiple_choice", "short_answer"]
    options: list[Option] = Field(default_factory=list, max_length=4)
    correct_answer: str = Field(min_length=1, max_length=200)
    accepted_answers: list[str] = Field(default_factory=list, max_length=6)
    evidence_ids: list[str] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def _shape_matches_kind(self) -> "CheckQuestion":
        ids = [o.option_id for o in self.options]
        if self.kind == "multiple_choice":
            if not 2 <= len(self.options) <= 4:
                raise ValueError("a multiple-choice question needs 2 to 4 options")
            if len(set(ids)) != len(ids):
                raise ValueError("option ids must be unique")
            if self.correct_answer not in ids:
                raise ValueError("correct_answer must be one of the option ids")
            if self.accepted_answers:
                raise ValueError("accepted_answers is for short answers only")
        elif self.options:
            raise ValueError("a short-answer question has no options")
        return self

    def correct_text(self) -> str:
        """The answer as a string a student could say: the option's text for a choice."""
        if self.kind == "multiple_choice":
            return next(o.text for o in self.options if o.option_id == self.correct_answer)
        return self.correct_answer


class TutorTurn(_Strict):
    explanation: str = Field(min_length=1, max_length=3000)
    cited_evidence_ids: list[str] = Field(min_length=1, max_length=8)
    check: Optional[CheckQuestion] = None


# ---------------------------------------------------------------- normalizing

def normalize(text: str) -> str:
    """Case, spacing and trailing punctuation folded; nothing looser. Positional phrasing
    ("the second one") depends on how options are presented, which is a product decision
    nobody made, so it is not guessed at (same rule as the earlier project's grader)."""
    return re.sub(r"\s+", " ", text.strip().casefold()).rstrip(".!?")


def _contains(haystack: str, needle: str) -> bool:
    """Whole-token containment, so the answer '2' is not 'found' inside '12'."""
    n = normalize(needle)
    return bool(n) and re.search(rf"(?<!\w){re.escape(n)}(?!\w)", normalize(haystack)) is not None


# ------------------------------------------------------------- code checks

def check_turn(turn: TutorTurn, handed: dict[str, Evidence]) -> list[str]:
    """Everything code can verify about a Tutor turn. Empty means it may be shown."""
    problems: list[str] = []
    unhanded = [e for e in turn.cited_evidence_ids if e not in handed]
    if unhanded:
        problems.append("cited_evidence_not_handed:" + ",".join(unhanded))

    check = turn.check
    if check is None:
        return problems

    q_unhanded = [e for e in check.evidence_ids if e not in handed]
    if q_unhanded:
        problems.append("question_evidence_not_handed:" + ",".join(q_unhanded))

    keys = [check.correct_text(), *check.accepted_answers]
    if any(_contains(turn.explanation, k) for k in keys):
        problems.append("private_answer_in_explanation")
    if check.kind == "short_answer" and any(_contains(check.prompt, k) for k in keys):
        problems.append("private_answer_in_question")

    cited_text = " ".join(handed[e].text for e in check.evidence_ids if e in handed)
    if not _contains(cited_text, check.correct_text()):
        problems.append("check_answer_not_in_cited_evidence")
    return problems


_EXPLAIN = {
    "cited_evidence_not_handed": "You cited passages you were not given",
    "question_evidence_not_handed": "The question cites passages you were not given",
    "private_answer_in_explanation": "Your explanation contains the answer to your check question. "
                                     "Rewrite it without giving that answer away",
    "private_answer_in_question": "Your question text contains its own answer. Rewrite the question",
    "check_answer_not_in_cited_evidence": "The correct answer does not appear verbatim in the evidence "
                                          "you cited for the question. Ask about something the evidence "
                                          "states in plain words, or offer no question",
}


def objections_from_problems(problems: list[str]) -> list[str]:
    """Objections for the Tutor. They never quote the answer key."""
    out = []
    for code in problems:
        head, _, detail = code.partition(":")
        sentence = _EXPLAIN.get(head, head)
        out.append(f"{sentence}: {detail}." if detail else f"{sentence}.")
    return out


# ------------------------------------------------------------------ grading

def resolve_option(check: CheckQuestion, submitted: str) -> Optional[str]:
    """Map what the student said onto an option: exact match on id or text, after folding."""
    s = normalize(submitted)
    if not s:
        return None
    for option in check.options:
        if s in (normalize(option.option_id), normalize(option.text)):
            return option.option_id
    return None


def grade(check: CheckQuestion, submitted: str) -> bool:
    """Deterministic. An answer that matches nothing is simply not the correct answer."""
    if check.kind == "multiple_choice":
        return resolve_option(check, submitted) == check.correct_answer
    accepted = {normalize(check.correct_answer), *(normalize(a) for a in check.accepted_answers)}
    return normalize(submitted) in accepted


# ------------------------------------------------------------------ handoff

def start_tutor_run(store: Store, notes_run_id: str) -> str:
    """Start a tutor run from a notes run that finished with a PASSED answer.

    The hand-off is scoped on purpose: the question, the answer text, and only the passages
    the answer cited. Never the drafts, objections or the ledger.
    """
    if store.get_state(notes_run_id) is not RunState.COMPLETE:
        raise ValueError("the notes run has not completed, so there is no checked answer to teach")
    draft = AnswerDraft.model_validate(store.latest(notes_run_id, "draft"))
    if draft.action != "answer":
        raise ValueError("the notes run ended in a stated gap: there is no answer to teach")
    passages = [p for p in store.latest(notes_run_id, "evidence")["passages"]
                if p["evidence_id"] in set(draft.cited_evidence_ids)]
    run_id = store.create_run("notes-tutor", {"source_run": notes_run_id})
    store.append(run_id, "handoff", {
        "question": store.latest(notes_run_id, "input")["text"],
        "answer_text": draft.text,
        "passages": passages,
    }, produced_by="system")
    return run_id


# ------------------------------------------------------------------ prompts

def _esc(text: str) -> str:
    return html.escape(text, quote=False)


def _attr(text: str) -> str:
    return html.escape(text, quote=True)


def build_tutor_messages(handoff: dict, prior: dict | None, objections: list[str]) -> list[dict]:
    evidence = ["EVIDENCE:"] + [
        f'<untrusted_evidence id="{_attr(p["evidence_id"])}" locator="{_attr(p["doc"])}#{p["ordinal"]}">'
        f'{_esc(p["text"])}</untrusted_evidence>' for p in handoff["passages"]]
    user = [
        f'QUESTION THE STUDENT ASKED:\n<untrusted_dialogue role="student">{_esc(handoff["question"])}</untrusted_dialogue>',
        "ANSWER ALREADY GIVEN (checked against the evidence):\n" + handoff["answer_text"],
        "\n".join(evidence),
    ]
    if prior and objections:
        user.append("YOUR PREVIOUS TURN:\n" + json.dumps(prior, indent=2))
        user.append("IT FAILED THESE CHECKS. Fix each one:\n" + "\n".join(f"- {o}" for o in objections))
    return [
        {"role": "system", "content": (_PROMPTS / "tutor.md").read_text(encoding="utf-8")},
        {"role": "user", "content": "\n\n---\n\n".join(user)},
    ]


# -------------------------------------------------------------------- flow

def _feedback(check: CheckQuestion, correct: bool, locators: str) -> str:
    """Shown only after the answer is final, so revealing the answer is allowed here."""
    if correct:
        return f"Correct. You can see this in {locators}."
    if check.kind == "short_answer":
        # Exact-match grading cannot tell a wrong answer from a right one worded differently, so it
        # must not say "wrong". (The earlier project graded only multiple choice deterministically
        # and left free text to judgment; this is the honest wording for the case we cannot judge.)
        return (f"That does not match the notes' wording. The notes give {check.correct_text()}. "
                f"See {locators}. If you meant the same thing, you have it.")
    return f"Not quite. The notes give {check.correct_text()}. See {locators}."


def build_flow(call=complete, should_stop=None):
    """`should_stop` is asked before the model call; True ends the run as "cancelled"."""

    def handle_drafting(ctx) -> RunState:
        """Only the model call. Checking happens in GATING, so a failed check is a real
        back-edge (GATING -> DRAFTING) and the runner never sees a state repeat itself."""
        if should_stop is not None and should_stop():
            ctx.append("failure", {"kind": "cancelled", "detail": "stopped before the Tutor's model call"},
                       produced_by="system")
            return RunState.FAILED
        handoff = ctx.latest("handoff")
        prior = ctx.latest("tutor_draft")
        last = ctx.latest("tutor_check")
        objections = objections_from_problems(last["problems"]) if prior and last else []

        turn = call(
            settings=ctx.settings, budget=ctx.budget,
            messages=build_tutor_messages(handoff, prior, objections),
            schema=TutorTurn, step="tutor",
        )
        ctx.append("tutor_draft", turn.model_dump(), produced_by="agent:tutor")
        return RunState.GATING

    def handle_gating(ctx) -> RunState:
        # GATING serves two moments. Before the student has answered there is no
        # expert_answer record: check the Tutor's turn. Once one exists (the student's
        # reply, or a timeout), grade it. The kit's states are reused as they are.
        if ctx.latest("expert_answer") is None:
            return _check_and_present(ctx)
        return _grade(ctx)

    def _check_and_present(ctx) -> RunState:
        handoff = ctx.latest("handoff")
        turn = TutorTurn.model_validate(ctx.latest("tutor_draft"))
        handed = {p["evidence_id"]: Evidence(**p) for p in handoff["passages"]}
        problems = check_turn(turn, handed)
        ctx.append("tutor_check", {"problems": problems}, produced_by="system:checks")

        if problems:
            if len(ctx.history("tutor_draft")) > MAX_TUTOR_REVISIONS:
                ctx.append("failure", {"kind": "tutor_exhausted",
                                       "detail": "The Tutor could not produce a turn that passed the checks."},
                           produced_by="system")
                return RunState.FAILED
            return RunState.DRAFTING

        check = turn.check
        if check is None:
            ctx.append("public_turn", {"explanation": turn.explanation, "question": None},
                       produced_by="system")
            return RunState.COMPLETE

        # The key is a separate, private record. Nothing below puts it anywhere public.
        ctx.append("answer_key", {"kind": check.kind, "correct_answer": check.correct_answer,
                                  "accepted_answers": check.accepted_answers,
                                  "evidence_ids": check.evidence_ids,
                                  "options": [o.model_dump() for o in check.options]},
                   produced_by="system")
        ctx.append("public_turn", {"explanation": turn.explanation,
                                   "question": {"prompt": check.prompt, "kind": check.kind,
                                                "options": [o.model_dump() for o in check.options]}},
                   produced_by="system")
        callback.ask(ctx.store, ctx.run_id, check.prompt,
                     {"resume_state": RunState.GATING.value, "kind": check.kind,
                      "options": [o.model_dump() for o in check.options]},
                     ctx.settings)
        return RunState.AWAITING_EXPERT

    def _grade(ctx) -> RunState:
        """The student's answer has arrived (or the wait timed out). No model call."""
        answer = ctx.latest("expert_answer")
        key = ctx.latest("answer_key")
        check = CheckQuestion.model_validate({
            "prompt": ctx.latest("public_turn")["question"]["prompt"],
            "kind": key["kind"], "options": key["options"], "correct_answer": key["correct_answer"],
            "accepted_answers": key["accepted_answers"], "evidence_ids": key["evidence_ids"]})

        if not answer.get("answer"):
            ctx.append("attempt", {"answered": False}, produced_by="system:grader")
            return RunState.COMPLETE

        correct = grade(check, answer["answer"])
        handoff = ctx.latest("handoff")
        locators = ", ".join(f'{p["doc"]}#{p["ordinal"]}' for p in handoff["passages"]
                             if p["evidence_id"] in check.evidence_ids)
        # Facts only. No mastery, no inference about the student.
        ctx.append("attempt", {"answered": True, "answer": answer["answer"], "correct": correct,
                               "evidence_ids": check.evidence_ids}, produced_by="system:grader")
        ctx.append("feedback", {"text": _feedback(check, correct, locators)}, produced_by="system")
        return RunState.COMPLETE

    return SimpleNamespace(
        name="notes-tutor",
        handlers={RunState.DRAFTING: handle_drafting, RunState.GATING: handle_gating},
    )


# -------------------------------------------------------------- student view

def public_view(store: Store, run_id: str) -> dict:
    """The ONLY thing a student-facing surface should render. The answer key is never in
    it, and the feedback (which may state the answer) appears only after the answer is in."""
    turn = store.latest(run_id, "public_turn")
    if turn is None:
        return {"explanation": None, "question": None, "feedback": None}
    question = dict(turn["question"]) if turn["question"] else None
    asked = store.latest(run_id, "question")
    if question and asked:
        question["id"] = asked["id"]
    feedback = store.latest(run_id, "feedback")
    return {"explanation": turn["explanation"], "question": question,
            "feedback": feedback["text"] if feedback else None}
