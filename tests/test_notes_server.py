"""The real server process: `scripts/notes.py serve`, over TCP, with a real access code.

The other API tests use an in-process client. This one starts the actual command, so it also
proves the pieces that only exist when it runs for real: argument handling, migrations on a
fresh database, uvicorn binding, and cookies across separate HTTP requests. Offline provider:
no model is called and no key is needed. The key is forced empty so it cannot spend anything.
"""
from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from demo.notes.questions import by_id

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = str(ROOT / "scripts" / "notes.py")
Q3 = by_id("q3-power").text
KEY = "one joule of energy per second"


def _env():
    return {**os.environ, "OPENROUTER_API_KEY": "", "PYTHONIOENCODING": "utf-8"}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _cli(tmp_path, *args):
    return subprocess.run([sys.executable, SCRIPT, *args], cwd=tmp_path, capture_output=True,
                          text=True, timeout=60, env=_env())


@pytest.fixture
def server(tmp_path):
    db = str(tmp_path / "app.db")
    token = re.search(r"nt_\S+", _cli(tmp_path, "token", "--name", "tester1", "--db", db).stdout).group(0)
    port = _free_port()
    process = subprocess.Popen([sys.executable, SCRIPT, "serve", "--db", db, "--port", str(port)],
                               cwd=tmp_path, env=_env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                if httpx.get(f"{base}/health/live", timeout=1).status_code == 200:
                    break
            except httpx.TransportError:
                time.sleep(0.2)
        else:
            process.kill()
            raise AssertionError("the server did not start: " + process.stderr.read())
        yield base, token
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


def test_the_real_server_answers_a_question_and_grades_the_check_over_http(server):
    base, token = server
    with httpx.Client(base_url=base, timeout=30) as client:
        assert client.get("/health/live").json() == {"status": "ok", "mode": "scripted"}

        login = client.post("/login", data={"token": token}, follow_redirects=False)
        assert login.status_code == 303 and client.cookies.get("nt_token") == token

        page = client.get("/ask").text
        rid = re.search(r'name="request_id" value="([^"]+)"', page).group(1)
        client.post("/ask", data={"question": Q3, "request_id": rid, "expected_version": "1"})

        page = client.get("/ask").text
        assert "20 W" in page and "A question to check" in page and KEY not in page.lower()
        rid, ver = (re.search(rf'name="{n}" value="([^"]+)"', page).group(1) for n in ("request_id", "expected_version"))
        client.post("/answer", data={"answer": KEY, "request_id": rid, "expected_version": ver})
        assert "Correct." in client.get("/ask").text


def test_the_real_server_refuses_the_api_without_a_code_and_accepts_it_with_one(server):
    base, token = server
    assert httpx.post(f"{base}/v1/sessions").status_code == 401
    ok = httpx.post(f"{base}/v1/sessions", headers={"Authorization": f"Bearer {token}"})
    assert ok.status_code == 201 and ok.json()["version"] == 1


def test_the_access_code_is_shown_once_and_only_its_hash_is_in_the_database(tmp_path):
    db = tmp_path / "app.db"
    out = _cli(tmp_path, "token", "--name", "tester1", "--db", str(db)).stdout
    token = re.search(r"nt_\S+", out).group(0)
    assert token.encode() not in db.read_bytes(), "the plaintext code was written to the database"


def test_serve_live_without_a_key_refuses_to_start(tmp_path):
    result = _cli(tmp_path, "serve", "--live", "--port", str(_free_port()), "--db", str(tmp_path / "a.db"))
    assert result.returncode == 2 and "OPENROUTER_API_KEY" in result.stderr
