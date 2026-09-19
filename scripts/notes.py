#!/usr/bin/env python3
"""Run and validate the notes slice.

    python scripts/notes.py validate                    # stub: scripted replies, free, proves wiring
    python scripts/notes.py validate --tutor            # ...and the Tutor turn for answered questions
    python scripts/notes.py validate --live --label a   # REAL models on OpenRouter: spends the team key
    python scripts/notes.py ask "your question" --live  # one live question, with a readable transcript
    python scripts/notes.py token --name tester1        # an access code for one tester (shown once)
    python scripts/notes.py serve                       # the tester page, offline demonstration
    python scripts/notes.py serve --live                # ...on the real models: spends the team key
    python scripts/notes.py compare out/a.json out/b.json
    python scripts/notes.py replay <run_id> --db out/a.db

Without --live nothing here calls a model, uses the network, or costs anything. With --live it
uses the OPENROUTER_API_KEY in .env and the kit's embedding search, so run it where the kit's
environment is (the Codespace). Live runs turn the fallback model off (--allow-fallback keeps
it) and run the gate on SLICE_ESCALATION_MODEL (--gate default uses the draft model).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from slice import runner
from slice.config import settings as load_settings
from slice.store import Store

from demo.notes import evaluate
from demo.notes.corpus import ingest_notes
from demo.notes.flow import build_flow
from demo.notes.questions import QUESTIONS
from demo.notes.probes import probe_markdown, run_probes
from demo.notes.validate import LiveNotReady, live_settings, run_validation, transcript

OUT = Path("out")


def cmd_validate(args) -> int:
    label = args.label or ("stub" if not args.live else time.strftime("live-%Y%m%d-%H%M%S"))
    only = args.only.split(",") if args.only else None
    n = len([q for q in QUESTIONS if not only or q.id in only])
    if args.live:
        print(f"LIVE run '{label}': {n} question(s), about 2 to 6 model calls each, on your OpenRouter key.")
    else:
        print(f"STUB run '{label}': scripted replies, no model is called. This proves wiring, not model behavior.")
    try:
        report = run_validation(live=args.live, settings=load_settings(), db_path=OUT / f"notes-{label}.db",
                                label=label, only=only, gate=args.gate,
                                allow_fallback=args.allow_fallback, with_tutor=args.tutor)
    except LiveNotReady as e:
        print(f"cannot run live: {e}", file=sys.stderr)
        return 2
    (OUT / f"notes-{label}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    md = evaluate.report_markdown(report)
    (OUT / f"notes-{label}.md").write_text(md, encoding="utf-8")
    print(md)
    print(f"wrote {OUT}/notes-{label}.json, .md and .db")
    return 0 if report["summary"]["failed"] == 0 and report["summary"]["not_evaluable"] == 0 else 1


def cmd_ask(args) -> int:
    if not args.live:
        print("ask needs --live (there is no scripted reply for free text). "
              "Use `validate` for the offline demonstration.", file=sys.stderr)
        return 2
    try:
        settings = live_settings(load_settings(), allow_fallback=args.allow_fallback)
        from slice.llm import complete
        db = OUT / f"notes-ask-{time.strftime('%H%M%S')}.db"
        db.parent.mkdir(parents=True, exist_ok=True)
        store = Store(str(db))
        ingest_notes(store)
    except (LiveNotReady, ImportError) as e:
        print(f"cannot run live: {e}", file=sys.stderr)
        return 2
    run_id = store.create_run("notes")
    store.append(run_id, "input", {"text": args.text}, produced_by="system")
    gate_model = settings.escalation_model if args.gate == "escalation" else None
    runner.advance(store, run_id, build_flow(call=complete, gate_model=gate_model), settings)
    print("\n".join(transcript(store, run_id)))
    print(f"\nrun {run_id}   replay: python scripts/notes.py replay {run_id} --db {db}")
    return 0


def cmd_probe_gate(args) -> int:
    """Hand the gate drafts that are wrong on purpose. Live only: a scripted gate proves nothing."""
    if not args.live:
        print("probe-gate needs --live: it asks the real gate model to judge 5 hand-written drafts "
              "(about 5 short calls). A scripted gate would only echo the script.", file=sys.stderr)
        return 2
    try:
        settings = live_settings(load_settings(), allow_fallback=args.allow_fallback)
    except LiveNotReady as e:
        print(f"cannot run live: {e}", file=sys.stderr)
        return 2
    from slice.llm import complete
    gate_model = settings.escalation_model if args.gate == "escalation" else None
    store = Store(str(OUT / "notes-probe.db"))
    report = run_probes(call=complete, settings=settings, store=store, gate_model=gate_model)
    md = probe_markdown(report)
    (OUT / "notes-probe.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (OUT / "notes-probe.md").write_text(md, encoding="utf-8")
    print(md)
    ok = report["wrong_drafts_blocked"] == report["wrong_drafts"] and report["control_passed"]
    return 0 if ok else 1


DEFAULT_APP_DB = OUT / "notes-app.db"


def cmd_token(args) -> int:
    """Issue an access code. Only its hash is stored, so this is the one time it can be shown."""
    from demo.notes import sessions as S
    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    store = Store(str(db_path))
    S.migrate(store.db)
    account = S.create_account(store.db, args.name)
    token = S.issue_token(store.db, account)
    print(f"account   {account}  ({args.name})")
    print(f"access code (shown once; give it only to {args.name}):")
    print()
    print(f"    {token}")
    print()
    return 0


def cmd_serve(args) -> int:
    from demo.notes.api import create_app
    from demo.notes.service import LiveProvider, NotesService, ScriptedProvider
    settings = load_settings()
    try:
        provider = (LiveProvider(settings, gate=args.gate, allow_fallback=args.allow_fallback)
                    if args.live else ScriptedProvider(settings))
    except LiveNotReady as e:
        print(f"cannot run live: {e}", file=sys.stderr)
        return 2
    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from demo.notes import tracing
        tracer = tracing.from_env(lambda run_id: tracing.run_to_trace(Store(str(db_path)), run_id))
        service = NotesService(str(db_path), provider, max_requests_per_hour=args.max_requests_per_hour,
                               tracer=tracer)
    except ImportError as e:            # the embedding search lives in the kit's environment
        print(f"cannot run live: retrieval needs the kit's environment ({e}).", file=sys.stderr)
        return 2
    accounts = service._open().db.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
    print(f"{'LIVE (spends the OpenRouter key)' if args.live else 'OFFLINE demonstration (scripted, free)'}"
          f" on http://{args.host}:{args.port}   database {db_path}")
    if accounts == 0:
        print("No access codes exist yet. In another terminal: python scripts/notes.py token --name tester1 "
              f"--db {db_path}")
    if args.host not in ("127.0.0.1", "localhost"):
        print("WARNING: this is reachable from other machines. Use https (a tunnel) so access codes and "
              "cookies are not sent in the clear, and give codes only to real testers.")
    import threading
    import uvicorn
    stop = threading.Event()
    print("Tracing to Arize AX: " + ("ON" if service.tracer else "off (set ARIZE_SPACE_ID and ARIZE_API_KEY)"))
    if service.tracer:
        threading.Thread(target=service.tracer.run_forever, args=(stop,), daemon=True).start()
    print("Tracing to Arize AX: " + ("ON" if service.tracer else "off (set ARIZE_SPACE_ID and ARIZE_API_KEY)"))
    if service.tracer:
        threading.Thread(target=service.tracer.run_forever, args=(stop,), daemon=True).start()
    if args.live:                       # uploads exist only live; the worker ingests and purges them
        threading.Thread(target=service.make_worker().run_forever, args=(stop,), daemon=True).start()
    uvicorn.run(create_app(service), host=args.host, port=args.port, log_level="warning")
    return 0


def cmd_compare(args) -> int:
    baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    candidate = json.loads(Path(args.candidate).read_text(encoding="utf-8"))
    cmp = evaluate.compare_reports(baseline, candidate)
    print(evaluate.comparison_markdown(cmp, baseline["label"], candidate["label"]))
    return 1 if cmp["regressions"] else 0


def cmd_replay(args) -> int:
    store = Store(args.db)
    print("\n".join(transcript(store, args.run_id)))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Run and validate the notes slice.")
    sub = p.add_subparsers(dest="cmd", required=True)

    def live_options(sp):
        sp.add_argument("--live", action="store_true", help="use the real models (spends the OpenRouter key)")
        sp.add_argument("--gate", choices=["escalation", "default"], default="escalation",
                        help="which model judges drafts in a live run")
        sp.add_argument("--allow-fallback", action="store_true",
                        help="keep the fallback model on (off by default so a live run tests one model)")

    v = sub.add_parser("validate", help="run the fixed questions and report")
    live_options(v)
    v.add_argument("--label", help="name for the report files")
    v.add_argument("--only", help="comma-separated question ids")
    v.add_argument("--tutor", action="store_true", help="also run the Tutor for answered questions")
    v.set_defaults(func=cmd_validate)

    a = sub.add_parser("ask", help="one live question with a transcript")
    live_options(a)
    a.add_argument("text")
    a.set_defaults(func=cmd_ask)

    g = sub.add_parser("probe-gate", help="feed the real gate deliberately wrong drafts (live only)")
    live_options(g)
    g.set_defaults(func=cmd_probe_gate)

    t = sub.add_parser("token", help="issue an access code for a tester (shown once)")
    t.add_argument("--name", required=True, help="who it is for, e.g. tester1")
    t.add_argument("--db", default=str(DEFAULT_APP_DB))
    t.set_defaults(func=cmd_token)

    sv = sub.add_parser("serve", help="run the tester page and API")
    live_options(sv)
    sv.add_argument("--db", default=str(DEFAULT_APP_DB))
    sv.add_argument("--host", default="127.0.0.1", help="127.0.0.1 keeps it on this machine")
    sv.add_argument("--port", type=int, default=8000)
    sv.add_argument("--max-requests-per-hour", type=int, default=60, dest="max_requests_per_hour",
                    help="per account; protects the shared key in live mode")
    sv.set_defaults(func=cmd_serve)

    c = sub.add_parser("compare", help="paired comparison of two reports")
    c.add_argument("baseline")
    c.add_argument("candidate")
    c.set_defaults(func=cmd_compare)

    r = sub.add_parser("replay", help="print a stored run")
    r.add_argument("run_id")
    r.add_argument("--db", required=True)
    r.set_defaults(func=cmd_replay)

    args = p.parse_args()
    # Real models write symbols (an ohm sign, a minus sign). The Windows console defaults to a
    # legacy code page that cannot print them, which crashed a live replay. Print UTF-8, and
    # never fail on a character the terminal cannot show.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    OUT.mkdir(exist_ok=True)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
