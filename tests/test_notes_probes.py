"""The gate probe: deliberately wrong drafts, and what the report says about a weak gate.

Scripted here (no model). Its value is that it cannot mistake a lenient gate for a good one
and cannot mistake a gate that blocks everything for a good one either.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from demo.notes.probes import probe_ledger_problems, probe_markdown, probes, run_probes
from demo.notes.stub import ScriptedModel
from slice.config import settings as load_settings
from slice.store import Store

ROOT = Path(__file__).resolve().parent.parent
BLOCK = json.dumps({"status": "BLOCK", "objections": [{"requirement_id": "r1", "problem": "wrong"}]})
PASS = json.dumps({"status": "PASS", "objections": []})


def _run(tmp_path, replies, gate_model=None):
    stub = ScriptedModel({"gate": replies})
    report = run_probes(call=stub, settings=load_settings(), store=Store(str(tmp_path / "p.db")),
                        gate_model=gate_model)
    return stub, report


def test_every_probe_is_clean_to_the_ledger_so_only_the_gate_is_being_tested():
    assert all(problems == [] for problems in probe_ledger_problems().values())


def test_there_are_four_wrong_drafts_and_one_correct_control():
    expected = [p.expect for p in probes()]
    assert expected.count("BLOCK") == 4 and expected.count("PASS") == 1


def test_a_gate_that_blocks_the_wrong_drafts_and_passes_the_control_is_reported_as_good(tmp_path):
    _, report = _run(tmp_path, [BLOCK, BLOCK, BLOCK, BLOCK, PASS])
    assert (report["wrong_drafts_blocked"], report["wrong_drafts"]) == (4, 4)
    assert report["control_passed"] is True
    assert all(r["correct"] for r in report["results"])


def test_a_lenient_gate_is_reported_as_missing_every_wrong_draft(tmp_path):
    _, report = _run(tmp_path, [PASS] * 5)
    assert report["wrong_drafts_blocked"] == 0 and report["control_passed"] is True
    assert "0 of 4 wrong drafts" in probe_markdown(report) and "**NO**" in probe_markdown(report)


def test_a_gate_that_blocks_everything_is_caught_by_the_control(tmp_path):
    _, report = _run(tmp_path, [BLOCK] * 5)
    assert report["wrong_drafts_blocked"] == 4 and report["control_passed"] is False
    assert "over-blocking" in probe_markdown(report)


def test_the_gate_model_asked_for_is_the_one_used_and_the_prompts_carry_the_wrong_draft(tmp_path):
    stub, report = _run(tmp_path, [BLOCK] * 4 + [PASS], gate_model="anthropic/claude-haiku-4.5")
    assert set(stub.models) == {"anthropic/claude-haiku-4.5"} and report["gate_model"] == "anthropic/claude-haiku-4.5"
    assert "The resistance is 3 ohms." in stub.messages[0][1]["content"]
    assert stub.calls == ["gate"] * 5


def test_objections_are_shown_in_the_report(tmp_path):
    _, report = _run(tmp_path, [BLOCK] * 4 + [PASS])
    assert "objection: wrong" in probe_markdown(report)


def _cli(tmp_path, *args):
    env = {**os.environ, "OPENROUTER_API_KEY": ""}   # a test must never spend the team's budget
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "notes.py"), *args],
                          cwd=tmp_path, capture_output=True, text=True, timeout=60, env=env)


def test_the_command_refuses_without_live_and_without_a_key_and_writes_nothing(tmp_path):
    no_live = _cli(tmp_path, "probe-gate")
    assert no_live.returncode == 2 and "needs --live" in no_live.stderr
    keyless = _cli(tmp_path, "probe-gate", "--live")
    assert keyless.returncode == 2 and "OPENROUTER_API_KEY" in keyless.stderr
    assert not list((tmp_path / "out").glob("notes-probe*"))
