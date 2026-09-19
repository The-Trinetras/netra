"""The two handlers, the domain rules, and the Flow object for the notes slice.

DRAFTING <-> GATING, with the same shape as demo/smoke/flow.py. What is ported from
the earlier project is the DESIGN: draft with citations, an evidence ledger that
code (not the model) uses to verify citations, and a gate that sends unsupported
work back. Business rules live here, in code; the model returns judgements and this
file decides what they mean.

Nothing in slice/ changes for this to run.

Two model calls per cycle at most: one draft, one gate. If the ledger already finds
a mechanical problem, the gate model is NOT called: there is nothing for it to judge
that code has not already rejected, and the call would cost tokens for no information.

Stop rules (ported from the earlier project's bounded turn): the revision limit, a cap
on model calls, a deadline, and a no-progress rule. Whichever fires, the run ends with a
recorded reason and an honest reply that claims nothing the notes were not shown to
support. Code decides every stop; the model never does.
"""
from __future__ import annotations

import html
import json
import time
from pathlib import Path
from types import SimpleNamespace

from slice.llm import complete
from slice.records import RunState

from .ledger import Evidence, EvidenceLedger
from .schema import AnswerDraft, Objection, Verdict

MAX_REVISIONS = 3
"""Drafts the gate may send back before the run stops. Counted from the record
history, not from budget.attempt(): that one is a spend fence and also ticks for
malformed-reply retries, so sharing a counter silently costs a student a revision."""

MAX_MODEL_CALLS = 6
"""Model calls (drafts + model gate verdicts) one run may spend: three full cycles. The
earlier project shared one such budget across retries, fallback and delegation; here it is
counted from the record, so it survives a resume. Retries inside slice.llm.complete are
not visible from a flow: they are bounded by the kit's token fence instead."""

DEADLINE_SECONDS = 90.0
"""Wall-clock time from the question being recorded. Checked before each model call, so a
slow provider stops the run instead of stretching it. The earlier project used 20 s for an
interactive answer; a revising loop over real models needs more, and this is a demo default."""

TOP_K = 3
"""Passages retrieved per question. Few on purpose: every passage is tokens in both
model calls, and a wrong answer with ten passages is harder to audit than with three."""

_PROMPTS = Path(__file__).parent / "prompts"


def _prompt(name: str) -> str:
    return (_PROMPTS / f"{name}.md").read_text(encoding="utf-8")


def _untrusted(text: str) -> str:
    """Escape data so it cannot close the tag that marks it as data."""
    return html.escape(text, quote=False)


def _attr(text: str) -> str:
    """Escape data placed inside an attribute value. Quotes must be escaped too: a file
    name is untrusted, and one containing a quote could otherwise forge attributes."""
    return html.escape(text, quote=True)


# ------------------------------------------------------------------ messages

def _evidence_block(evidence: list[Evidence]) -> str:
    if not evidence:
        return "VALIDATED EVIDENCE:\n- none retrieved"
    lines = ["VALIDATED EVIDENCE:"]
    for e in evidence:
        lines.append(
            f'<untrusted_evidence id="{_attr(e.evidence_id)}" '
            f'locator="{_attr(e.locator)}">{_untrusted(e.text)}</untrusted_evidence>')
    return "\n".join(lines)


def build_draft_messages(question: str, evidence: list[Evidence], prior: dict | None,
                         objections: list[dict]) -> list[dict]:
    user = [f'QUESTION:\n<untrusted_dialogue role="student">{_untrusted(question)}</untrusted_dialogue>',
            _evidence_block(evidence)]
    if prior and objections:
        user.append("YOUR PREVIOUS DRAFT:\n" + json.dumps(prior, indent=2))
        user.append("IT WAS SENT BACK. Fix each of these:\n\n" + "\n".join(
            f"- {o.get('requirement_id') or 'draft'}: {o['problem']}" for o in objections))
    return [
        {"role": "system", "content": _prompt("draft")},
        {"role": "user", "content": "\n\n---\n\n".join(user)},
    ]


def build_gate_messages(question: str, evidence: list[Evidence], draft: dict) -> list[dict]:
    user = [f'QUESTION:\n<untrusted_dialogue role="student">{_untrusted(question)}</untrusted_dialogue>',
            _evidence_block(evidence),
            "DRAFT TO JUDGE:\n" + json.dumps(draft, indent=2)]
    return [
        {"role": "system", "content": _prompt("gate")},
        {"role": "user", "content": "\n\n---\n\n".join(user)},
    ]


# ------------------------------------------------------------ ledger -> text

_EXPLAIN = {
    "no_validated_evidence_cited": "The answer cites no evidence. Cite the evidence_id of each passage you relied on.",
    "cited_evidence_not_validated": "These ids are not among the retrieved passages, so they cite nothing",
    "requirement_unresolved": "This requirement is not supported by any retrieved passage",
    "assessment_for_undeclared_requirement": "You assessed a requirement you never declared",
    "gap_stated_but_every_requirement_supported": "You stated a gap, but every requirement is marked supported",
}


def objections_from_problems(problems: list[str]) -> list[Objection]:
    """Turn the ledger's short codes into objections the drafter can act on."""
    out = []
    for code in problems:
        head, _, detail = code.partition(":")
        req, _, extra = detail.partition(":") if head == "requirement_unresolved" else (None, "", detail)
        sentence = _EXPLAIN.get(head, head)
        if head == "requirement_unresolved":
            out.append(Objection(requirement_id=req, problem=f"{sentence} ({extra})."))
        elif detail:
            out.append(Objection(problem=f"{sentence}: {detail}."))
        else:
            out.append(Objection(problem=sentence))
    return out


def limited_reply(ledger: EvidenceLedger, draft: AnswerDraft | None, reason: str) -> str:
    """What the student is told when the run stops without a passing answer.

    Ported from the earlier project's "limited" outcome. It deliberately does NOT list
    claims as supported: the ledger only proves a citation is real, not that the text says
    what the draft claims (that is the gate's job, and the gate may be what stopped us).
    It says where the evidence was and what could not be established, and it does not guess.
    """
    parts = ["I stopped before I could give you a checked answer."]
    if ledger.evidence:
        where = ", ".join(sorted({e.locator for e in ledger.evidence.values()}))
        parts.append(f"I looked at your notes ({where}) but could not confirm an answer from them.")
    else:
        parts.append("I found nothing in your notes that answers this.")
    if draft is not None:
        ledger.check_draft(draft)
        open_reqs = [s.requirement.description for s in ledger.open_requirements()]
        if open_reqs:
            parts.append("Not established: " + "; ".join(open_reqs) + ".")
    parts.append("I won't guess. You can rephrase the question or point me to another part of your notes.")
    return " ".join(parts)


def _default_search(store, query: str, k: int):
    from .corpus import search_notes
    return search_notes(store, query, k=k)


# ------------------------------------------------------------------ handlers

def build_flow(call=complete, search=_default_search, now=time.time,
               max_model_calls: int = MAX_MODEL_CALLS, deadline_seconds: float = DEADLINE_SECONDS,
               gate_model: str | None = None, should_stop=None):
    """Return the Flow. `call`, `search` and `now` are injected so the whole state machine
    runs with canned replies, canned passages and a fake clock: no key, no network, no
    embeddings, no waiting. `gate_model` runs the gate on a different (usually stronger) model
    than the drafter; None means the kit's default model, as for the draft. `should_stop` is asked
    before every model call: when it returns True the run ends as "cancelled" (a student pressed
    STOP, or left). A call already in flight cannot be interrupted, so its result is simply never
    used."""

    def _model_calls_used(ctx) -> int:
        drafts = len(ctx.history("draft"))
        gates = sum(1 for v in ctx.history("verdict") if v.produced_by == "agent:gate")
        return drafts + gates

    def _limit_reached(ctx) -> str | None:
        """Checked BEFORE a model call, so refusing to start is what costs nothing."""
        if should_stop is not None and should_stop():
            return "cancelled"
        if _model_calls_used(ctx) >= max_model_calls:
            return "model_calls"
        started = ctx.history("input")[0].created_at
        if now() - started >= deadline_seconds:
            return "deadline"
        return None

    def _stop(ctx, kind: str, detail: str) -> RunState:
        evidence = ctx.latest("evidence")
        ledger = EvidenceLedger()
        if evidence:
            ledger.add_evidence(Evidence(**c) for c in evidence["passages"])
        latest = ctx.latest("draft")
        draft = AnswerDraft.model_validate(latest) if latest else None
        ctx.append("failure", {"kind": kind, "detail": detail,
                               "reply": limited_reply(ledger, draft, kind)}, produced_by="system")
        return RunState.FAILED

    def _evidence(ctx) -> list[Evidence]:
        return [Evidence(**c) for c in ctx.latest("evidence")["passages"]]

    def handle_drafting(ctx) -> RunState:
        question = ctx.latest("input")["text"]
        limit = _limit_reached(ctx)
        if limit:
            return _stop(ctx, limit, "Stopped before a draft: " + limit.replace("_", " ") + " limit reached.")

        if ctx.latest("evidence") is None:            # retrieve once, on the first pass
            passages = [Evidence.from_chunk(c) for c in search(ctx.store, question, TOP_K)]
            ctx.append("evidence",
                       {"passages": [vars(p) for p in passages]}, produced_by="system:retrieval")
        evidence = _evidence(ctx)

        prior = ctx.latest("draft")
        verdict = ctx.latest("verdict")
        objections = verdict["objections"] if prior and verdict and verdict["status"] == "BLOCK" else []

        draft = call(
            settings=ctx.settings, budget=ctx.budget,
            messages=build_draft_messages(question, evidence, prior, objections),
            schema=AnswerDraft, step="draft",
        )
        ctx.append("draft", draft.model_dump(), produced_by="agent:draft")
        return RunState.GATING

    def handle_gating(ctx) -> RunState:
        question = ctx.latest("input")["text"]
        evidence = _evidence(ctx)
        draft = AnswerDraft.model_validate(ctx.latest("draft"))

        ledger = EvidenceLedger()
        ledger.add_evidence(evidence)
        problems = ledger.check_draft(draft)

        if problems:
            # Code found a mechanical defect: no model call, the objection is certain.
            verdict = Verdict(status="BLOCK", objections=objections_from_problems(problems))
            ctx.append("verdict", verdict.model_dump(), produced_by="system:ledger")
        else:
            limit = _limit_reached(ctx)
            if limit:
                return _stop(ctx, limit, "Stopped before the gate: " + limit.replace("_", " ") + " limit reached.")
            verdict = call(
                settings=ctx.settings, budget=ctx.budget,
                messages=build_gate_messages(question, evidence, draft.model_dump()),
                schema=Verdict, step="gate", model=gate_model,
            )
            ctx.append("verdict", verdict.model_dump(), produced_by="agent:gate")

        if verdict.status == "PASS":
            return RunState.COMPLETE

        # A revision identical to the draft before it will be blocked for the same reason
        # again. This is the earlier project's no-progress rule: an action that cannot
        # produce anything new is refused, so the run stops here instead of spending
        # another cycle to learn nothing.
        drafts = ctx.history("draft")
        if len(drafts) >= 2 and drafts[-1].payload == drafts[-2].payload:
            return _stop(ctx, "no_progress",
                         "The revision was identical to the draft before it, so another cycle "
                         "would be blocked for the same reasons.")

        # Counted from the record, not from the budget. See MAX_REVISIONS.
        blocks = sum(1 for v in ctx.history("verdict") if v.payload["status"] == "BLOCK")
        if blocks >= MAX_REVISIONS:
            return _stop(ctx, "gate_exhausted", f"Blocked {blocks} times; no revision passed.")
        return RunState.DRAFTING

    return SimpleNamespace(
        name="notes",
        handlers={
            RunState.DRAFTING: handle_drafting,
            RunState.GATING: handle_gating,
        },
    )
