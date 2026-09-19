"""The service: a session-safe question, a check question, and the guarantees around them.

Runs the real flows on scripted replies (the offline provider). Nothing here calls a model.
"""
from __future__ import annotations

import dataclasses
import json

import pytest

from demo.notes import sessions as S
from demo.notes.questions import by_id
from demo.notes.service import LiveProvider, NoScript, NotesService, ScriptedProvider, ServiceError
from demo.notes.stub import ScriptedModel
from demo.notes.validate import LiveNotReady
from slice.config import settings as load_settings

Q3 = by_id("q3-power").text
Q1 = by_id("q1-resistance-from-table").text
Q5 = by_id("q5-not-covered-parallel").text
KEY = "one joule of energy per second"     # the Tutor's private answer for the q3 check question


@pytest.fixture
def svc(tmp_path):
    return NotesService(str(tmp_path / "n.db"), ScriptedProvider(load_settings()))


@pytest.fixture
def me(svc):
    account = S.create_account(svc._open().db, "asha")
    return account, svc.create_session(account).session_id


def _runs(svc, domain):
    return svc._open().db.execute("SELECT COUNT(*) FROM runs WHERE domain=?", (domain,)).fetchone()[0]


# ------------------------------------------------------------------- asking

def test_a_question_returns_a_cited_answer_an_explanation_and_a_check_question(svc, me):
    account, sid = me
    result, snap, replayed = svc.ask(account, sid, "req-1", Q3, 1)

    assert result["status"] == "answered" and "20 W" in result["answer"] and replayed is False
    assert result["sources"] == ["electrical-power-notes.md#0"]
    assert result["explanation"] and result["check_question"]["prompt"]
    assert snap["version"] == 2 and snap["waiting_for_answer"] is True
    assert snap["check_question"]["prompt"] == result["check_question"]["prompt"]


def test_the_answer_key_never_appears_in_anything_returned_before_the_student_answers(svc, me):
    account, sid = me
    result, snap, _ = svc.ask(account, sid, "req-1", Q3, 1)
    everything = json.dumps([result, snap, svc.snapshot(account, sid)]).lower()
    assert KEY not in everything and "correct_answer" not in everything and "accepted_answers" not in everything


def test_the_back_edge_runs_inside_a_session(svc, me):
    """q1's scripted first draft is wrong; the gate sends it back; the student only sees the fixed one."""
    account, sid = me
    result, _, _ = svc.ask(account, sid, "req-1", Q1, 1)
    assert result["status"] == "answered" and "2 ohms" in result["answer"] and "3 ohms" not in result["answer"]


def test_a_question_the_notes_do_not_cover_is_reported_honestly_and_asks_no_check(svc, me):
    account, sid = me
    result, snap, _ = svc.ask(account, sid, "req-1", Q5, 1)
    assert result["status"] == "not_in_notes" and "series" in result["text"]
    assert snap["waiting_for_answer"] is False and snap["check_question"] is None


def test_internal_run_ids_are_not_exposed(svc, me):
    account, sid = me
    result, snap, _ = svc.ask(account, sid, "req-1", Q3, 1)
    assert "run_" not in json.dumps([result, snap])


# --------------------------------------------------------- the check question

def test_a_correct_answer_is_graded_and_clears_the_waiting_question(svc, me):
    account, sid = me
    svc.ask(account, sid, "req-1", Q3, 1)
    result, snap, _ = svc.answer_check(account, sid, "req-2", KEY, 2)

    assert result["status"] == "graded" and result["correct"] is True
    assert result["feedback"].startswith("Correct.")
    assert snap["version"] == 3 and snap["waiting_for_answer"] is False


def test_a_wrong_answer_reveals_the_answer_only_now_that_it_is_final(svc, me):
    account, sid = me
    svc.ask(account, sid, "req-1", Q3, 1)
    result, _, _ = svc.answer_check(account, sid, "req-2", "a lot", 2)
    assert result["correct"] is False and KEY in result["feedback"]


def test_answering_when_no_question_is_waiting_is_refused_and_changes_nothing(svc, me):
    account, sid = me
    with pytest.raises(ServiceError) as info:
        svc.answer_check(account, sid, "req-1", "anything", 1)
    assert info.value.code == "no_question_waiting"
    assert svc.snapshot(account, sid)["version"] == 1


# ------------------------------------------------------- idempotency + versions

def test_retrying_a_question_replays_it_and_does_not_run_the_agent_again(svc, me):
    account, sid = me
    first, _, _ = svc.ask(account, sid, "req-1", Q3, 1)
    again, snap, replayed = svc.ask(account, sid, "req-1", Q3, 1)
    assert replayed is True and again == first and snap["version"] == 2
    assert _runs(svc, "notes") == 1, "the retry ran the agent a second time"


def test_reusing_a_request_id_for_a_different_question_is_refused(svc, me):
    account, sid = me
    svc.ask(account, sid, "req-1", Q3, 1)
    with pytest.raises(S.RequestIdReused):
        svc.ask(account, sid, "req-1", Q5, 1)


def test_a_stale_version_is_refused_before_any_work_is_done(svc, me):
    account, sid = me
    svc.ask(account, sid, "req-1", Q5, 1)
    before = _runs(svc, "notes")
    with pytest.raises(S.VersionConflict):
        svc.ask(account, sid, "req-2", Q3, 1)
    assert _runs(svc, "notes") == before


# --------------------------------------------------------------------- cancel

class _CancellingProvider:
    """Presses STOP while the first model call is running."""

    def __init__(self, inner, ref):
        self.inner, self.ref, self.settings = inner, ref, inner.settings
        self.gate_model, self.mode = inner.gate_model, "scripted"

    def prepare(self, store):
        return None

    def for_question(self, text):
        parts = self.inner.for_question(text)
        real = parts["call"]
        self.ref["model"] = real

        def call(**kwargs):
            reply = real(**kwargs)
            account, sid = self.ref["who"]
            self.ref["svc"].cancel(account, sid)
            return reply
        return {**parts, "call": call}


def test_stop_during_a_question_delivers_nothing_and_leaves_the_session_untouched(tmp_path):
    ref: dict = {}
    provider = _CancellingProvider(ScriptedProvider(load_settings()), ref)
    svc = NotesService(str(tmp_path / "n.db"), provider)
    account = S.create_account(svc._open().db, "asha")
    sid = svc.create_session(account).session_id
    ref.update(svc=svc, who=(account, sid))

    result, snap, _ = svc.ask(account, sid, "req-1", Q3, 1)
    assert result == {"status": "cancelled"}, "an answer was delivered after STOP"
    assert snap["version"] == 1 and snap["waiting_for_answer"] is False
    # Two layers enforce STOP and each is checked on its own: this is the early one (no further
    # model call is spent once STOP is pressed); the commit-time one is tested below.
    assert ref["model"].calls == ["draft"], "the gate was called after STOP"

    again, _, replayed = svc.ask(account, sid, "req-1", Q3, 1)
    assert again == {"status": "cancelled"} and replayed is True, "a retry must not revive a stopped request"


def test_stop_with_nothing_running_says_so(svc, me):
    account, sid = me
    assert svc.cancel(account, sid) is False


# ------------------------------------------------------------ isolation + input

def test_another_account_can_do_nothing_with_my_session(svc, me):
    account, sid = me
    svc.ask(account, sid, "req-1", Q3, 1)
    intruder = S.create_account(svc._open().db, "intruder")
    with pytest.raises(S.NotFound):
        svc.snapshot(intruder, sid)
    with pytest.raises(S.NotFound):
        svc.ask(intruder, sid, "req-x", Q5, 2)
    with pytest.raises(S.NotFound):
        svc.answer_check(intruder, sid, "req-y", KEY, 2)
    with pytest.raises(S.NotFound):
        svc.cancel(intruder, sid)


@pytest.mark.parametrize("question, code", [
    ("", "empty_question"), ("   ", "empty_question"), ("x" * 501, "question_too_long")])
def test_bad_questions_are_refused_with_a_reason(svc, me, question, code):
    account, sid = me
    with pytest.raises(ServiceError) as info:
        svc.ask(account, sid, "req-1", question, 1)
    assert info.value.code == code and svc.snapshot(account, sid)["version"] == 1


@pytest.mark.parametrize("request_id", ["", "a b", "x" * 81, "req;drop table", "<script>"])
def test_unsafe_request_ids_are_refused(svc, me, request_id):
    account, sid = me
    with pytest.raises(ServiceError) as info:
        svc.ask(account, sid, request_id, Q3, 1)
    assert info.value.code == "bad_request_id"


def test_a_free_text_question_is_refused_offline_and_changes_nothing(svc, me):
    account, sid = me
    with pytest.raises(NoScript):
        svc.ask(account, sid, "req-1", "What is a watt?", 1)
    assert svc.snapshot(account, sid)["version"] == 1


# --------------------------------------------------------------- the Tutor

def test_a_tutor_that_cannot_produce_a_valid_turn_leaves_the_answer_in_place(tmp_path):
    class BadTutor(ScriptedProvider):
        def for_question(self, text):
            parts = super().for_question(text)
            bad = json.dumps({"explanation": "x", "cited_evidence_ids": ["never-handed"], "check": None})
            return {**parts, "tutor_call": ScriptedModel({"tutor": [bad] * 3})}

    svc = NotesService(str(tmp_path / "n.db"), BadTutor(load_settings()))
    account = S.create_account(svc._open().db, "asha")
    sid = svc.create_session(account).session_id
    result, snap, _ = svc.ask(account, sid, "req-1", Q3, 1)

    assert result["status"] == "answered" and "20 W" in result["answer"]
    assert result["explanation"] is None and result["check_question"] is None
    assert snap["waiting_for_answer"] is False


def test_the_tutor_can_be_switched_off(tmp_path):
    svc = NotesService(str(tmp_path / "n.db"), ScriptedProvider(load_settings()), tutor=False)
    account = S.create_account(svc._open().db, "asha")
    sid = svc.create_session(account).session_id
    result, snap, _ = svc.ask(account, sid, "req-1", Q3, 1)
    assert result["explanation"] is None and result["check_question"] is None


# ----------------------------------------------------------------- live guard

def test_a_live_provider_refuses_to_start_without_a_key():
    keyless = dataclasses.replace(load_settings(), api_key="")
    with pytest.raises(LiveNotReady, match="OPENROUTER_API_KEY"):
        LiveProvider(keyless)


def test_a_cancelled_run_is_reported_as_cancelled_by_the_service_itself(svc, me):
    """The session layer also discards a cancelled result at commit, which would hide a service
    that reported it wrongly. Call the service's own step directly, with the flag already set."""
    account, sid = me
    db = svc._open().db
    view = S.get_session(db, account, sid)
    db.execute("INSERT INTO session_requests(session_id, request_id, fingerprint, status, cancel_requested, created_at) "
               "VALUES (?,?,?,?,?,?)", (sid, "req-1", "fp", "running", 1, 0))
    state, result = svc._answer_question(db, view, "req-1", Q3)
    assert result == {"status": "cancelled"} and state["pending_check"] is None


# --------------------------------------------------------------- rate limit

def _limited(tmp_path, limit):
    svc = NotesService(str(tmp_path / "n.db"), ScriptedProvider(load_settings()), max_requests_per_hour=limit)
    account = S.create_account(svc._open().db, "asha")
    return svc, account, svc.create_session(account).session_id


def test_an_account_over_its_hourly_limit_is_refused_before_any_model_call(tmp_path):
    svc, account, sid = _limited(tmp_path, 2)
    svc.ask(account, sid, "req-1", Q5, 1)
    svc.ask(account, sid, "req-2", Q5, 2)
    before = _runs(svc, "notes")
    with pytest.raises(ServiceError) as info:
        svc.ask(account, sid, "req-3", Q5, 3)
    assert info.value.code == "rate_limited" and _runs(svc, "notes") == before
    assert svc.snapshot(account, sid)["version"] == 3, "a refused request must not change the session"


def test_a_replay_is_still_answered_when_the_account_is_over_its_limit(tmp_path):
    svc, account, sid = _limited(tmp_path, 1)
    first, _, _ = svc.ask(account, sid, "req-1", Q5, 1)
    again, _, replayed = svc.ask(account, sid, "req-1", Q5, 1)
    assert replayed is True and again == first


def test_requests_older_than_an_hour_and_other_accounts_do_not_count(tmp_path):
    svc, account, sid = _limited(tmp_path, 1)
    svc.ask(account, sid, "req-1", Q5, 1)
    svc._open().db.execute("UPDATE session_requests SET created_at = created_at - 7200")
    svc.ask(account, sid, "req-2", Q5, 2)                     # the old one no longer counts

    other = S.create_account(svc._open().db, "other")
    other_sid = svc.create_session(other).session_id
    svc.ask(other, other_sid, "req-9", Q5, 1)                 # someone else's usage is separate


def test_a_request_refused_for_the_limit_does_not_use_up_more_of_it(tmp_path):
    svc, account, sid = _limited(tmp_path, 1)
    svc.ask(account, sid, "req-1", Q5, 1)
    for i in range(3):
        with pytest.raises(ServiceError):
            svc.ask(account, sid, f"req-x{i}", Q5, 2)
    svc._open().db.execute("UPDATE session_requests SET created_at = created_at - 7200 WHERE request_id='req-1'")
    svc.ask(account, sid, "req-after", Q5, 2)                 # only failed rows remain in the window


def test_the_offline_demo_offers_a_multiple_choice_check_for_q1(svc, me):
    account, sid = me
    result, _, _ = svc.ask(account, sid, "req-1", Q1, 1)
    check = result["check_question"]
    assert check["kind"] == "multiple_choice" and [o["text"] for o in check["options"]] == [
        "ohmic", "reactive", "inductive"]
    graded, _, _ = svc.answer_check(account, sid, "req-2", "opt-1", 2)
    assert graded["correct"] is True
