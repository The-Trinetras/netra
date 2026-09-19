"""The HTTP surface: JSON API and tester page, on the offline provider.

Nothing here calls a model. The properties tested are the ones that keep a web surface safe:
authentication on every route, errors that never echo input or exception text, other accounts'
sessions being invisible, hostile model output being escaped, and cookies/forms that resist
cross-site abuse.
"""
from __future__ import annotations

import json
import logging
import re

import pytest
from fastapi.testclient import TestClient

from demo.notes import canned, sessions as S
from demo.notes.api import create_app
from demo.notes.questions import by_id
from demo.notes.service import NotesService, ScriptedProvider
from demo.notes.stub import ScriptedModel
from slice.config import settings as load_settings

Q3 = by_id("q3-power").text
Q5 = by_id("q5-not-covered-parallel").text
KEY = "one joule of energy per second"
SECRET = "PRIVATE-MARKER"   # a traceback prints source lines, so a secret is built from this name


@pytest.fixture
def svc(tmp_path):
    return NotesService(str(tmp_path / "n.db"), ScriptedProvider(load_settings()))


def _token(svc, name="asha"):
    db = svc._open().db
    return S.issue_token(db, S.create_account(db, name))


@pytest.fixture
def client(svc):
    return TestClient(create_app(svc))


@pytest.fixture
def token(svc):
    return _token(svc)


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _session(client, token):
    return client.post("/v1/sessions", headers=_auth(token)).json()["session_id"]


def _turn(client, token, sid, question=Q3, request_id="req-1", version=1):
    return client.post(f"/v1/sessions/{sid}/turns", headers=_auth(token), json={
        "request_id": request_id, "question": question, "expected_session_version": version})


def _runs(svc):
    return svc._open().db.execute("SELECT COUNT(*) FROM runs WHERE domain='notes'").fetchone()[0]


# ------------------------------------------------------------------ JSON: auth

def test_health_is_open_and_says_which_mode_it_is_in(client):
    assert client.get("/health/live").json() == {"status": "ok", "mode": "scripted"}


ROUTES = [("post", "/v1/sessions"), ("get", "/v1/sessions/sess_x"),
          ("post", "/v1/sessions/sess_x/turns"), ("post", "/v1/sessions/sess_x/answers"),
          ("post", "/v1/sessions/sess_x/cancel")]


@pytest.mark.parametrize("method, path", ROUTES)
def test_every_api_route_refuses_a_missing_or_wrong_token(client, method, path):
    for headers in ({}, _auth("nt_not-a-real-token"), {"Authorization": "Basic abc"}):
        response = getattr(client, method)(path, headers=headers,
                                           **({"json": {}} if method == "post" else {}))
        assert response.status_code == 401 and response.json()["error"]["code"] == "unauthorized"


def test_a_revoked_token_stops_working(svc, client, token):
    sid = _session(client, token)
    S.revoke_token(svc._open().db, token)
    assert client.get(f"/v1/sessions/{sid}", headers=_auth(token)).status_code == 401


# ------------------------------------------------------------ JSON: the flow

def test_ask_then_answer_the_check_question(client, token):
    sid = _session(client, token)
    turn = _turn(client, token, sid)
    body = turn.json()
    assert turn.status_code == 200 and body["result"]["status"] == "answered"
    assert "20 W" in body["result"]["answer"] and body["session"]["version"] == 2 and body["replayed"] is False

    graded = client.post(f"/v1/sessions/{sid}/answers", headers=_auth(token), json={
        "request_id": "req-2", "answer": KEY, "expected_session_version": 2}).json()
    assert graded["result"]["correct"] is True and graded["session"]["version"] == 3


def test_a_retry_replays_and_the_agent_is_not_run_again(svc, client, token):
    sid = _session(client, token)
    first = _turn(client, token, sid).json()
    again = _turn(client, token, sid).json()
    assert again["replayed"] is True and again["result"] == first["result"] and _runs(svc) == 1


def test_a_stale_version_is_a_conflict_that_reports_the_current_version(client, token):
    sid = _session(client, token)
    _turn(client, token, sid, question=Q5, request_id="req-1", version=1)
    stale = _turn(client, token, sid, request_id="req-2", version=1)
    assert stale.status_code == 409
    assert stale.json()["error"] == {"code": "version_conflict", "current_version": 2,
                                     "message": "The session changed. Reload it and try again."}


def test_reusing_a_request_id_for_a_different_question_is_a_conflict(client, token):
    sid = _session(client, token)
    _turn(client, token, sid, question=Q3, request_id="req-1")
    reused = _turn(client, token, sid, question=Q5, request_id="req-1")
    assert reused.status_code == 409 and reused.json()["error"]["code"] == "request_id_reused"


def test_answering_with_no_question_waiting_is_a_clear_error(client, token):
    sid = _session(client, token)
    response = client.post(f"/v1/sessions/{sid}/answers", headers=_auth(token), json={
        "request_id": "req-1", "answer": "x", "expected_session_version": 1})
    assert response.status_code == 400 and response.json()["error"]["code"] == "no_question_waiting"


def test_cancel_reports_whether_anything_was_running(client, token):
    sid = _session(client, token)
    assert client.post(f"/v1/sessions/{sid}/cancel", headers=_auth(token)).json() == {"cancelled": False}


def test_free_text_is_refused_offline_with_a_reason(client, token):
    sid = _session(client, token)
    response = _turn(client, token, sid, question="What is a watt?")
    assert response.status_code == 422 and response.json()["error"]["code"] == "not_available_offline"


# ---------------------------------------------------- JSON: isolation + safety

def test_another_accounts_session_looks_exactly_like_one_that_does_not_exist(svc, client, token):
    sid = _session(client, token)
    intruder = _token(svc, "intruder")
    real = client.get(f"/v1/sessions/{sid}", headers=_auth(intruder))
    fake = client.get("/v1/sessions/sess_does_not_exist", headers=_auth(intruder))
    assert real.status_code == fake.status_code == 404 and real.json() == fake.json()
    for response in (_turn(client, intruder, sid),
                     client.post(f"/v1/sessions/{sid}/cancel", headers=_auth(intruder))):
        assert response.status_code == 404


def test_an_invalid_body_is_refused_without_echoing_what_was_sent(client, token):
    sid = _session(client, token)
    response = client.post(f"/v1/sessions/{sid}/turns", headers=_auth(token), json={
        "request_id": "r", "question": "q", "expected_session_version": 1, "secret": SECRET})
    assert response.status_code == 400 and response.json()["error"]["code"] == "invalid_request"
    assert SECRET not in response.text


def test_an_unexpected_error_returns_a_generic_500_and_logs_type_and_frames_not_the_message(svc, token, caplog):
    class Exploding(ScriptedProvider):
        def for_question(self, text):
            raise RuntimeError(SECRET + " the student question and the answer key")

    boom = NotesService(svc.db_path, Exploding(load_settings()))
    client = TestClient(create_app(boom), raise_server_exceptions=False)
    sid = _session(client, token)
    with caplog.at_level(logging.ERROR, logger="notes.api"):
        response = _turn(client, token, sid)
    assert response.status_code == 500 and response.json()["error"]["code"] == "internal_error"
    assert SECRET not in response.text and SECRET not in caplog.text
    assert "RuntimeError" in caplog.text and ".py" in caplog.text


def test_responses_carry_security_headers(client, token):
    for response in (client.get("/health/live"), client.get("/"), _turn(client, token, _session(client, token))):
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["cache-control"] == "no-store"
        assert "default-src 'none'" in response.headers["content-security-policy"]
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


# ---------------------------------------------------------------- the page

def _login(client, token):
    return client.post("/login", data={"token": token}, follow_redirects=False)


def _page(client, path="/ask"):
    return client.get(path).text


def test_the_sign_in_page_is_accessible_markup_with_no_script(client):
    html = client.get("/").text
    assert '<html lang="en">' in html and "<title>" in html and 'class="skip"' in html
    assert re.search(r'<label for="token">', html) and 'id="token"' in html and "<main" in html
    assert "<script" not in html.lower()


def test_a_wrong_access_code_is_refused_with_an_alert_and_sets_no_cookie(client):
    response = _login(client, "nt_wrong")
    assert response.status_code == 401 and 'role="alert"' in response.text
    assert "set-cookie" not in response.headers


def test_signing_in_sets_httponly_samesite_strict_cookies_and_never_puts_the_token_in_a_url(client, token):
    response = _login(client, token)
    assert response.status_code == 303 and response.headers["location"] == "/ask"
    cookies = " ".join(v for k, v in response.headers.multi_items() if k == "set-cookie").lower()
    assert "httponly" in cookies and "samesite=strict" in cookies
    assert token not in response.headers["location"]


def test_asking_on_the_page_shows_the_answer_sources_and_check_question_but_not_the_key(client, token):
    _login(client, token)
    page = _page(client)
    request_id = re.search(r'name="request_id" value="([^"]+)"', page).group(1)
    version = re.search(r'name="expected_version" value="(\d+)"', page).group(1)
    posted = client.post("/ask", data={"question": Q3, "request_id": request_id, "expected_version": version},
                         follow_redirects=False)
    assert posted.status_code == 303

    html = _page(client)
    assert "20 W" in html and "electrical-power-notes.md#0" in html and "A question to check" in html
    assert KEY not in html.lower() and "correct_answer" not in html
    assert 'name="answer"' in html and 'for="answer"' in html


def test_answering_the_check_question_on_the_page_shows_the_outcome_and_a_fresh_ask_form(client, token):
    _login(client, token)
    page = _page(client)
    rid, ver = (re.search(rf'name="{n}" value="([^"]+)"', page).group(1) for n in ("request_id", "expected_version"))
    client.post("/ask", data={"question": Q3, "request_id": rid, "expected_version": ver})
    page = _page(client)
    rid, ver = (re.search(rf'name="{n}" value="([^"]+)"', page).group(1) for n in ("request_id", "expected_version"))
    client.post("/answer", data={"answer": KEY, "request_id": rid, "expected_version": ver})

    html = _page(client)
    assert "Correct." in html and 'name="question"' in html and 'name="answer"' not in html


def test_submitting_the_same_form_twice_does_not_run_the_agent_twice(svc, client, token):
    _login(client, token)
    page = _page(client)
    data = {"question": Q3, "expected_version": "1",
            "request_id": re.search(r'name="request_id" value="([^"]+)"', page).group(1)}
    client.post("/ask", data=data)
    client.post("/ask", data=data)
    assert _runs(svc) == 1


def test_hostile_model_output_is_escaped_on_the_page(tmp_path):
    class Hostile(ScriptedProvider):
        def for_question(self, text):
            parts = super().for_question(text)
            chunk = canned._find("electrical-power-notes.md", "20 W")
            draft = canned._answer(chunk, "<script>alert(1)</script> <img src=x onerror=alert(2)> 20 W", "x")
            return {**parts, "call": ScriptedModel({"draft": [draft], "gate": [canned.PASS]}),
                    "tutor_call": None}

    svc = NotesService(str(tmp_path / "n.db"), Hostile(load_settings()))
    client = TestClient(create_app(svc))
    _login(client, _token(svc))
    page = _page(client)
    rid = re.search(r'name="request_id" value="([^"]+)"', page).group(1)
    client.post("/ask", data={"question": Q3, "request_id": rid, "expected_version": "1"})

    html = _page(client)
    assert "<script>alert" not in html and "<img src=x" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_a_cross_site_form_post_is_refused_and_does_no_work(svc, client, token):
    _login(client, token)
    page = _page(client)
    rid = re.search(r'name="request_id" value="([^"]+)"', page).group(1)
    response = client.post("/ask", data={"question": Q3, "request_id": rid, "expected_version": "1"},
                           headers={"Origin": "http://evil.example"})
    assert response.status_code == 400 and "That request was refused." in response.text
    assert _runs(svc) == 0


def test_an_oversized_form_is_refused(client, token):
    _login(client, token)
    response = client.post("/ask", data={"question": "x" * 20_000, "request_id": "r", "expected_version": "1"})
    assert response.status_code == 400 and "refused" in response.text


def test_a_tampered_session_cookie_cannot_reach_another_accounts_session(svc, client, token):
    victim_token = _token(svc, "victim")
    victim_client = TestClient(create_app(svc))
    _login(victim_client, victim_token)
    victim_session = victim_client.cookies.get("nt_session")

    _login(client, token)
    client.cookies.set("nt_session", victim_session)
    response = client.get("/ask", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/"


def test_signing_out_clears_the_cookies_and_the_page_needs_a_login_again(client, token):
    _login(client, token)
    assert client.post("/logout", follow_redirects=False).status_code == 303
    assert client.get("/ask", follow_redirects=False).headers["location"] == "/"


def test_starting_a_new_session_resets_the_conversation(client, token):
    _login(client, token)
    page = _page(client)
    rid = re.search(r'name="request_id" value="([^"]+)"', page).group(1)
    client.post("/ask", data={"question": Q5, "request_id": rid, "expected_version": "1"})
    assert "Not in your notes" in _page(client)
    client.post("/new")
    fresh = _page(client)
    assert "Not in your notes" not in fresh and 'value="1"' in fresh


@pytest.mark.parametrize("path", ["/", "/ask"])
def test_no_page_ever_contains_a_script_tag(client, token, path):
    _login(client, token)
    assert "<script" not in client.get(path).text.lower()


def test_the_api_reports_the_limit_as_429(tmp_path):
    svc = NotesService(str(tmp_path / "n.db"), ScriptedProvider(load_settings()), max_requests_per_hour=1)
    client = TestClient(create_app(svc))
    token = _token(svc)
    sid = _session(client, token)
    assert _turn(client, token, sid, question=Q5, request_id="a", version=1).status_code == 200
    limited = _turn(client, token, sid, question=Q5, request_id="b", version=2)
    assert limited.status_code == 429 and limited.json()["error"]["code"] == "rate_limited"


def test_the_referrer_policy_lets_a_browser_send_its_real_origin_on_a_form_post(client):
    """With Referrer-Policy: no-referrer a browser sends "Origin: null" on same-site form posts, and
    the cross-site check then locks everyone out of signing in. This was found only by using the page
    in a real browser. The header must stay same-origin, and a literal "null" origin stays refused."""
    assert client.get("/").headers["referrer-policy"] == "same-origin"
    refused = client.post("/login", data={"token": "x"}, headers={"Origin": "null"})
    assert refused.status_code == 400 and "That request was refused." in refused.text


# ------------------------------------------- the check question on the page

Q1 = by_id("q1-resistance-from-table").text


def _ask_on_page(client, question):
    page = _page(client)
    rid, ver = (re.search(rf'name="{n}" value="([^"]+)"', page).group(1) for n in ("request_id", "expected_version"))
    client.post("/ask", data={"question": question, "request_id": rid, "expected_version": ver})
    return _page(client)


def _answer_on_page(client, answer):
    page = _page(client)
    rid, ver = (re.search(rf'name="{n}" value="([^"]+)"', page).group(1) for n in ("request_id", "expected_version"))
    client.post("/answer", data={"answer": answer, "request_id": rid, "expected_version": ver})
    return _page(client)


def test_a_multiple_choice_check_is_a_labelled_radio_group_not_a_text_box(client, token):
    _login(client, token)
    html = _ask_on_page(client, Q1)
    assert "<fieldset>" in html and "<legend>What is a resistor called" in html
    radios = re.findall(r'<input type="radio" id="(opt\d)" name="answer" value="([^"]+)" required>', html)
    assert [value for _, value in radios] == ["opt-1", "opt-2", "opt-3"]
    assert all(f'<label for="{rid}"' in html for rid, _ in radios), "every option needs a label"
    assert 'type="text"' not in html.split("A question to check")[1].split("</form>")[0]


def test_choosing_the_right_option_is_graded_correct_with_the_verdict_shown_once(client, token):
    _login(client, token)
    _ask_on_page(client, Q1)
    html = _answer_on_page(client, "opt-1")
    assert html.count("Correct.") == 1, "the verdict was printed twice"


def test_choosing_a_wrong_option_says_not_quite_once_and_reveals_the_answer(client, token):
    _login(client, token)
    _ask_on_page(client, Q1)
    html = _answer_on_page(client, "opt-2")
    assert html.count("Not quite.") == 1 and "The notes give ohmic" in html


def test_a_short_answer_that_does_not_match_never_tells_the_student_they_are_wrong(client, token):
    """Exact-match grading cannot tell a wrong answer from a right one worded differently."""
    _login(client, token)
    _ask_on_page(client, Q3)
    html = _answer_on_page(client, "one joule per second")
    assert "does not match the notes" in html and "Not quite" not in html
    assert "If you meant the same thing, you have it." in html


def test_the_ask_form_tells_the_tester_the_wait_is_normal(client, token):
    """A live answer took 10 to 30 seconds and the page has no spinner (no JavaScript). Measured, not
    guessed: without this note a tester would think the page had frozen and press Ask again."""
    _login(client, token)
    html = _page(client)
    assert 'id="wait"' in html and "up to half a minute" in html and "do not press Ask again" in html


# ------------------------------------------------------------- notes page

def _sign_in(client, token):
    r = client.post("/login", data={"token": token}, follow_redirects=False)
    assert r.status_code == 303


def test_the_notes_page_needs_sign_in_and_lists_only_this_accounts_notes(svc, client):
    assert client.get("/sources", follow_redirects=False).status_code == 303
    assert client.post("/sources", data={"name": "x", "text": "y"}, follow_redirects=False).status_code == 303
    from demo.notes import sources as SRC
    db = svc._open().db
    other = S.create_account(db, "other")
    SRC.create_source(db, other, "OtherPrivateNotes", "o.md", "text")
    _sign_in(client, _token(svc))
    page = client.get("/sources")
    assert page.status_code == 200 and "Your notes" in page.text and "OtherPrivateNotes" not in page.text


def test_offline_the_page_offers_no_upload_and_the_post_is_refused_without_storing(svc, client):
    _sign_in(client, _token(svc))
    assert "Uploads need the live mode" in client.get("/sources").text
    r = client.post("/sources", data={"name": "n", "text": "some text"}, follow_redirects=False)
    assert r.status_code == 400 and "live mode" in r.text
    assert svc._open().db.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 0


def test_a_cross_site_upload_or_delete_is_refused(svc, client):
    _sign_in(client, _token(svc))
    for path in ("/sources", "/sources/delete"):
        r = client.post(path, data={"name": "n", "text": "t"}, headers={"origin": "http://evil.example"})
        assert r.status_code == 400 and "refused" in r.text
