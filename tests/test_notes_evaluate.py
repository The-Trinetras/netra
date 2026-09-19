"""The deterministic assertions, the run report, the comparison, and the offline validation.

Everything here runs on scripted replies (stub mode). What it proves is that the checks
catch what they claim to catch and that a stub report can never pass for a live one. It says
nothing about any model: only `scripts/notes.py validate --live` does, and that needs the key.
"""
from __future__ import annotations

import copy
import dataclasses
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from demo.notes import canned, evaluate
from demo.notes.flow import build_flow
from demo.notes.questions import QUESTIONS, by_id
from demo.notes.stub import ScriptedModel
from demo.notes.validate import (
    LiveNotReady,
    live_settings,
    run_config,
    run_question,
    run_validation,
    transcript,
)
from slice import runner
from slice.config import settings as load_settings
from slice.records import RunState
from slice.store import Store

ROOT = Path(__file__).resolve().parent.parent
Q1, Q2, Q5 = (by_id(i) for i in ("q1-resistance-from-table", "q2-series-current", "q5-not-covered-parallel"))


def _run_scripted(tmp_path, question, model=None, search=None):
    store = Store(str(tmp_path / "t.db"))
    if model is None:
        model, search = canned.scripted(question)
    run_id = store.create_run("notes")
    store.append(run_id, "input", {"text": question.text}, produced_by="system")
    runner.advance(store, run_id, build_flow(call=model, search=search), load_settings())
    return store, run_id


def _gate_lets_it_through(chunk_id: str, text: str, action="answer") -> str:
    return json.dumps({"action": action,
                       "requirements": [{"requirement_id": "r1", "description": "x"}],
                       "assessments": [{"requirement_id": "r1", "status": "supported", "evidence_id": chunk_id}],
                       "text": text, "cited_evidence_ids": [chunk_id]})


# --------------------------------------------------------------- assertions

def test_a_good_run_passes_every_check(tmp_path):
    store, run_id = _run_scripted(tmp_path, Q1)
    result = evaluate.evaluate_run(store, run_id, Q1)
    assert result.status == "passed" and all(c.passed for c in result.checks)
    assert (result.drafts, result.blocks) == (2, 1)


def test_a_wrong_answer_that_a_lenient_gate_lets_through_is_still_caught(tmp_path):
    """The assertions do not trust the gate. A gate that passes '3 ohms' is a defect the
    deterministic check exists to surface."""
    model, search = canned.scripted(Q1)
    chunk = search(None, "", 1)[0]
    lenient = ScriptedModel({"draft": [_gate_lets_it_through(chunk.chunk_id, "The resistance is 3 ohms.")],
                             "gate": [json.dumps({"status": "PASS", "objections": []})]})
    store, run_id = _run_scripted(tmp_path, Q1, lenient, search)
    result = evaluate.evaluate_run(store, run_id, Q1)

    assert result.status == "failed"
    failed = [c.name for c in result.checks if not c.passed]
    assert failed == ["states_2 ohm"]


def test_an_answer_that_cites_the_wrong_note_fails(tmp_path):
    model, search = canned.scripted(by_id("q3-power"))
    chunk = search(None, "", 1)[0]                       # a power-notes chunk
    wrong = ScriptedModel({"draft": [_gate_lets_it_through(chunk.chunk_id, "The resistance is 2 ohms.")],
                           "gate": [json.dumps({"status": "PASS", "objections": []})]})
    store, run_id = _run_scripted(tmp_path, Q1, wrong, search)
    result = evaluate.evaluate_run(store, run_id, Q1)
    assert "cites_ohms-law-notes.md" in [c.name for c in result.checks if not c.passed]


def test_answering_a_question_the_notes_do_not_cover_fails(tmp_path):
    model, search = canned.scripted(Q5)
    chunk = search(None, "", 1)[0]
    bluffing = ScriptedModel({"draft": [_gate_lets_it_through(chunk.chunk_id, "The current is 4.17 A.")],
                              "gate": [json.dumps({"status": "PASS", "objections": []})]})
    store, run_id = _run_scripted(tmp_path, Q5, bluffing, search)
    result = evaluate.evaluate_run(store, run_id, Q5)
    assert result.status == "failed"
    assert [c.name for c in result.checks if not c.passed] == ["stated_a_gap"]


def test_a_stopped_run_is_a_failure_with_its_reason_not_a_pass(tmp_path):
    same = _gate_lets_it_through("ohms-law-notes.md:0", "The resistance is 3 ohms.")
    block = json.dumps({"status": "BLOCK", "objections": [{"problem": "wrong"}]})
    model = ScriptedModel({"draft": [same, same], "gate": [block, block]})
    _, search = canned.scripted(Q1)
    store, run_id = _run_scripted(tmp_path, Q1, model, search)
    result = evaluate.evaluate_run(store, run_id, Q1)

    assert result.status == "failed" and result.final_state == "failed"
    assert result.stop_kind == "no_progress"
    assert result.answer_text.startswith("I stopped before I could give you a checked answer.")


def test_an_unfinished_run_is_not_evaluable_never_a_pass(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    run_id = store.create_run("notes")
    store.append(run_id, "input", {"text": Q1.text}, produced_by="system")
    result = evaluate.evaluate_run(store, run_id, Q1)
    assert result.status == "not_evaluable"


def test_word_start_matching_finds_units_but_not_fragments():
    assert evaluate._has("The resistance is 2 ohms.", "2 ohm")
    assert not evaluate._has("The current is 12 A.", "2 A")
    assert evaluate._has("the CURRENT doubles", "current")


# ------------------------------------------------------------ report + hashes

def test_the_fingerprint_is_stable_and_changes_when_a_prompt_changes(monkeypatch):
    assert evaluate.fingerprint() == evaluate.fingerprint()
    original = evaluate.fingerprint()["prompts_sha256"]
    real_read = Path.read_text
    monkeypatch.setattr(Path, "read_text",
                        lambda self, *a, **k: real_read(self, *a, **k) + "\nEXTRA"
                        if self.name == "gate.md" else real_read(self, *a, **k))
    assert evaluate.fingerprint()["prompts_sha256"] != original


def test_a_stub_report_says_so_in_its_mode_and_its_markdown(tmp_path):
    report = run_validation(live=False, settings=load_settings(), db_path=tmp_path / "r.db", label="t")
    assert report["mode"] == "stub"
    assert "scripted (stub) run" in evaluate.report_markdown(report)
    assert "no model was called" in report["config"]["models"]


def test_the_report_never_contains_the_api_key(tmp_path):
    settings = dataclasses.replace(load_settings(), api_key="sk-or-v1-SECRET-KEY-VALUE")
    report = run_validation(live=False, settings=settings, db_path=tmp_path / "r.db", label="t")
    assert "SECRET-KEY-VALUE" not in json.dumps(report)


def test_the_live_config_records_the_models_used_and_never_the_key():
    settings = dataclasses.replace(load_settings(), api_key="sk-or-v1-SECRET-KEY-VALUE",
                                   model="m/draft", escalation_model="m/gate", fallback_model="")
    config = run_config(
        settings, live=True, gate_model="m/gate", with_tutor=False)
    assert config["draft_model"] == "m/draft" and config["gate_model"] == "m/gate"
    assert config["fallback_model"] == "off"
    assert "SECRET-KEY-VALUE" not in json.dumps(config)


# ------------------------------------------------------------- offline run

def test_the_offline_validation_passes_all_six_and_the_loop_goes_backwards(tmp_path):
    report = run_validation(live=False, settings=load_settings(), db_path=tmp_path / "r.db", label="t",
                            with_tutor=True)
    assert report["summary"]["passed"] == 6 and report["summary"]["total"] == 6
    q1 = next(c for c in report["cases"] if c["question_id"] == Q1.id)
    assert (q1["drafts"], q1["blocks"]) == (2, 1)
    assert set(report["tutor"]) == {"q1-resistance-from-table", "q2-series-current",
                                    "q3-power", "q4-misconception"}
    assert all(t["asked_a_question"] and t["failure"] is None for t in report["tutor"].values())


def test_only_selects_questions_and_an_unknown_id_is_an_error(tmp_path):
    report = run_validation(live=False, settings=load_settings(), db_path=tmp_path / "r.db", label="t",
                            only=["q2-series-current"])
    assert [c["question_id"] for c in report["cases"]] == ["q2-series-current"]
    with pytest.raises(ValueError):
        run_validation(live=False, settings=load_settings(), db_path=tmp_path / "r2.db", label="t",
                       only=["nope"])


def test_the_transcript_shows_the_draft_the_block_and_the_final_state(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    run_id, _, _ = run_question(store, Q1, live=False, settings=load_settings())
    text = "\n".join(transcript(store, run_id))
    assert "draft     [answer] The resistance is 3 ohms." in text
    assert "gate      BLOCK" in text and "gate      PASS" in text
    assert text.rstrip().endswith("final     complete")


# ------------------------------------------------------------ live safeguards

def test_a_live_run_without_a_key_refuses_before_doing_anything(tmp_path):
    keyless = dataclasses.replace(load_settings(), api_key="")
    with pytest.raises(LiveNotReady, match="OPENROUTER_API_KEY"):
        run_validation(live=True, settings=keyless, db_path=tmp_path / "r.db", label="t")
    assert not (tmp_path / "r.db").exists(), "nothing should be created before the refusal"


def test_live_runs_switch_the_fallback_model_off_unless_asked():
    settings = dataclasses.replace(load_settings(), api_key="k", fallback_model="some/fallback")
    assert live_settings(settings, allow_fallback=False).fallback_model == ""
    assert live_settings(settings, allow_fallback=True).fallback_model == "some/fallback"


def test_the_gate_can_run_on_a_different_model_than_the_drafter(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    model, search = canned.scripted(Q2)
    run_id = store.create_run("notes")
    store.append(run_id, "input", {"text": Q2.text}, produced_by="system")
    flow = build_flow(call=model, search=search, gate_model="anthropic/claude-haiku-4.5")
    runner.advance(store, run_id, flow, load_settings())
    assert model.calls == ["draft", "gate"]
    assert model.models == [None, "anthropic/claude-haiku-4.5"], "only the gate asks for the strong model"


# ------------------------------------------------------------- comparison

def _report(tmp_path, label):
    return run_validation(live=False, settings=load_settings(), db_path=tmp_path / f"{label}.db", label=label)


def test_a_regression_is_listed_and_not_netted_against_an_improvement(tmp_path):
    base = _report(tmp_path, "base")
    cand = copy.deepcopy(base)
    cand["label"] = "cand"
    cand["cases"][1]["status"] = "failed"             # q2 regresses
    base2 = copy.deepcopy(base)
    base2["cases"][2]["status"] = "failed"            # q3 was failing in the baseline...
    cmp = evaluate.compare_reports(base2, cand)        # ...and passes in the candidate

    assert [r["question_id"] for r in cmp["regressions"]] == ["q2-series-current"]
    assert [r["question_id"] for r in cmp["improvements"]] == ["q3-power"]
    assert cmp["paired_cases"] == 6
    assert "REGRESSION q2-series-current" in evaluate.comparison_markdown(cmp, "base", "cand")


def test_reports_from_different_prompts_or_modes_are_flagged_as_not_like_for_like(tmp_path):
    base = _report(tmp_path, "base")
    cand = copy.deepcopy(base)
    cand["fingerprint"]["prompts_sha256"] = "different"
    cand["mode"] = "live"
    notes = " ".join(evaluate.compare_reports(base, cand)["notes"])
    assert "prompts_sha256 differs" in notes and "not comparable" in notes


def test_cases_present_in_only_one_report_are_reported_not_silently_dropped(tmp_path):
    base = _report(tmp_path, "base")
    cand = copy.deepcopy(base)
    dropped = cand["cases"].pop()
    cmp = evaluate.compare_reports(base, cand)
    assert cmp["only_in_baseline"] == [dropped["question_id"]] and cmp["paired_cases"] == 5


# ------------------------------------------------------------ the real script

def _cli(tmp_path, *args):
    # The key is forced empty: a test must never be able to spend the team's budget, whatever
    # is in the environment it runs in (a Codespace secret, a developer's shell).
    env = {**os.environ, "OPENROUTER_API_KEY": ""}
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "notes.py"), *args],
                          cwd=tmp_path, capture_output=True, text=True, timeout=120, env=env)


def test_the_script_runs_the_offline_demonstration_and_writes_a_report(tmp_path):
    result = _cli(tmp_path, "validate", "--label", "demo")
    assert result.returncode == 0, result.stderr
    assert "STUB run" in result.stdout and "Passed 6 / 6" in result.stdout
    assert json.loads((tmp_path / "out" / "notes-demo.json").read_text())["mode"] == "stub"
    assert (tmp_path / "out" / "notes-demo.db").exists()


def test_the_script_refuses_ask_without_live_and_live_without_a_key(tmp_path):
    ask = _cli(tmp_path, "ask", "what is a watt?")
    assert ask.returncode == 2 and "needs --live" in ask.stderr

    keyless = _cli(tmp_path, "validate", "--live")
    assert keyless.returncode == 2
    assert "cannot run live" in keyless.stderr and "OPENROUTER_API_KEY" in keyless.stderr
    assert list((tmp_path / "out").glob("notes-live*")) == [], "a refused live run must leave no report"


def test_the_script_can_replay_a_stored_run_and_compare_two_reports(tmp_path):
    _cli(tmp_path, "validate", "--label", "a")
    _cli(tmp_path, "validate", "--label", "b")
    same = _cli(tmp_path, "compare", str(tmp_path / "out/notes-a.json"), str(tmp_path / "out/notes-b.json"))
    assert same.returncode == 0 and "Regressions: 0" in same.stdout

    worse = json.loads((tmp_path / "out/notes-b.json").read_text())
    worse["cases"][0]["status"] = "failed"
    (tmp_path / "out/notes-worse.json").write_text(json.dumps(worse))
    regressed = _cli(tmp_path, "compare", str(tmp_path / "out/notes-a.json"), str(tmp_path / "out/notes-worse.json"))
    assert regressed.returncode == 1 and "REGRESSION" in regressed.stdout

    store = Store(str(tmp_path / "out/notes-a.db"))
    first_run = store.db.execute("SELECT id FROM runs ORDER BY created_at LIMIT 1").fetchone()["id"]
    replayed = _cli(tmp_path, "replay", first_run, "--db", str(tmp_path / "out/notes-a.db"))
    assert replayed.returncode == 0 and "question" in replayed.stdout


def test_replay_prints_symbols_a_real_model_writes_even_on_a_legacy_console(tmp_path):
    """A live model wrote an ohm sign and the Windows console (cp1252) could not print it, which
    crashed the replay. The script must print such text, whatever the terminal's encoding."""
    (tmp_path / "out").mkdir(exist_ok=True)
    store = Store(str(tmp_path / "out" / "notes-sym.db"))
    run_id = store.create_run("notes")
    store.append(run_id, "input", {"text": "What is the resistance?"}, produced_by="system")
    store.append(run_id, "draft", {"action": "answer", "text": "R = 5 Ω and 3 − 1 = 2",
                                   "requirements": [], "assessments": [], "cited_evidence_ids": []},
                 produced_by="agent:draft")
    env = {**os.environ, "OPENROUTER_API_KEY": "", "PYTHONIOENCODING": "cp1252"}
    result = subprocess.run([sys.executable, str(ROOT / "scripts" / "notes.py"), "replay", run_id,
                             "--db", str(tmp_path / "out" / "notes-sym.db")],
                            cwd=tmp_path, capture_output=True, timeout=60, env=env)
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert "Ω".encode("utf-8") in result.stdout
