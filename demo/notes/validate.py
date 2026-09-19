"""Run the notes slice (and optionally the Tutor) over the fixed questions, and report.

Two modes, and the difference matters:

  stub  scripted replies from demo/notes/canned.py. No key, no network, no tokens. It
        proves the wiring. It says nothing about any model, and its report says "stub".
  live  the real models through slice.llm.complete, i.e. OpenRouter. It spends the team's
        capped key. Retrieval is the kit's real embedding search.

Live runs follow the model policy: the fallback model is OFF by default (a transient error
should fail visibly, not silently test a different model than the one being validated), and
the gate can run on the escalation model. The report records exactly which.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path

from slice import runner
from slice.budget import Budget
from slice.records import RunState
from slice.store import Store

from . import canned, evaluate
from . import tutor as tutor_mod
from .corpus import ingest_notes
from .flow import build_flow
from .questions import QUESTIONS, Question


class LiveNotReady(RuntimeError):
    """A live run was asked for but cannot start (no key, or no embedding support)."""


def live_settings(settings, *, allow_fallback: bool):
    """Live settings. The fallback is dropped unless explicitly kept."""
    if not settings.api_key:
        raise LiveNotReady("no OPENROUTER_API_KEY. Put the team key in .env (never in code or a commit).")
    return settings if allow_fallback else dataclasses.replace(settings, fallback_model="")


def run_config(settings, *, live: bool, gate_model: str | None, with_tutor: bool) -> dict:
    """What a report says it was made with. Never includes the key."""
    if not live:
        return {"models": "scripted replies (no model was called)", "tutor": with_tutor}
    return {"draft_model": settings.model, "gate_model": gate_model or settings.model,
            "fallback_model": settings.fallback_model or "off", "max_tokens": settings.max_tokens,
            "tutor": with_tutor}


def _tutor_summary(store: Store, notes_run: str, question: Question, *, live: bool, settings) -> dict:
    if live:
        from slice.llm import complete
        call = complete
    else:
        call = canned.scripted_tutor(question.id)
    run_id = tutor_mod.start_tutor_run(store, notes_run)
    state = runner.advance(store, run_id, tutor_mod.build_flow(call=call), settings)
    view = tutor_mod.public_view(store, run_id)
    failure = store.latest(run_id, "failure")
    return {"run_id": run_id, "state": state.value,
            "explained": bool(view["explanation"]), "asked_a_question": view["question"] is not None,
            "failure": failure["kind"] if failure else None}


def run_question(store: Store, question: Question, *, live: bool, settings,
                 gate_model: str | None = None, with_tutor: bool = False):
    """One question through the flow. Returns (run_id, CaseResult, tutor summary or None)."""
    run_id = store.create_run("notes")
    store.append(run_id, "input", {"text": question.text}, produced_by="system")
    if live:
        from slice.llm import complete
        flow = build_flow(call=complete, gate_model=gate_model)
    else:
        model, search = canned.scripted(question)
        flow = build_flow(call=model, search=search, gate_model=gate_model)
    runner.advance(store, run_id, flow, settings)

    tokens = int(Budget(store, run_id, settings).tokens_used())
    result = evaluate.evaluate_run(store, run_id, question, tokens=tokens)
    tutor = None
    if with_tutor and result.status == "passed" and question.outcome == "answer":
        tutor = _tutor_summary(store, run_id, question, live=live, settings=settings)
    return run_id, result, tutor


def run_validation(*, live: bool, settings, db_path: str | Path, label: str, only: list[str] | None = None,
                   gate: str = "escalation", allow_fallback: bool = False, with_tutor: bool = False) -> dict:
    """Run the chosen questions and return the report. Nothing is written except the run database."""
    if live:
        settings = live_settings(settings, allow_fallback=allow_fallback)
    gate_model = settings.escalation_model if (live and gate == "escalation") else None

    questions = [q for q in QUESTIONS if not only or q.id in only]
    if not questions:
        raise ValueError(f"no fixed question matches {only}")

    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    store = Store(str(db_path))
    if live:
        try:
            ingest_notes(store)
        except ImportError as e:      # fastembed / sqlite-vec live in the kit's Codespace image
            raise LiveNotReady(f"retrieval needs the kit's environment ({e}). Run this in the Codespace.") from e

    results, tutors = [], {}
    for question in questions:
        _, result, tutor = run_question(store, question, live=live, settings=settings,
                                        gate_model=gate_model, with_tutor=with_tutor)
        results.append(result)
        if tutor:
            tutors[question.id] = tutor
    report = evaluate.build_report(label, "live" if live else "stub",
                                   run_config(settings, live=live, gate_model=gate_model, with_tutor=with_tutor),
                                   results)
    if with_tutor:
        report["tutor"] = tutors
    return report


def transcript(store: Store, run_id: str) -> list[str]:
    """A readable account of one run, from the append-only record."""
    lines = []
    for v in store.replay(run_id):
        p = v.payload
        if v.kind == "input":
            lines.append(f"question  {p['text']}")
        elif v.kind == "evidence":
            lines.append("evidence  " + ", ".join(f"{x['doc']}#{x['ordinal']}" for x in p["passages"]))
        elif v.kind == "draft":
            lines.append(f"draft     [{p['action']}] {p['text']}")
        elif v.kind == "verdict":
            by = "code" if v.produced_by == "system:ledger" else "gate"
            objections = "; ".join(o["problem"] for o in p["objections"])
            lines.append(f"{by:<9} {p['status']}" + (f": {objections}" if objections else ""))
        elif v.kind == "failure":
            lines.append(f"stopped   {p['kind']}: {p.get('reply', p['detail'])}")
    lines.append(f"final     {store.get_state(run_id).value}")
    return lines
