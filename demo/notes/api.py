"""The HTTP surface: a small JSON API and a server-rendered tester page.

Ported from the earlier project's API layer, scaled to this slice. Kept: every route is
authenticated and authorized by account; errors are typed and never echo the caller's input
or an exception message; an unexpected error is logged by type and stack frames only (a
message can carry private text, which the earlier project learned the hard way); a retry with
the same request id replays instead of repeating; a stale version is a conflict, not an overwrite.

Not ported: the WebSocket protocol, audio, and reconnect. Text only, request/response.

The page has no JavaScript, like the kit's expert page: it works on a phone with one bar of
signal and with a screen reader, which is the audience the earlier project was built for. It has
not been tested with NVDA or any assistive technology; that needs a person and is still owed.

    python scripts/notes.py token --name tester1        # once per tester; the token prints once
    python scripts/notes.py serve                       # offline demonstration (six fixed questions)
    python scripts/notes.py serve --live                # the real models: spends the team key
"""
from __future__ import annotations

import html
import logging
import traceback
import uuid
from typing import Any
from urllib.parse import parse_qs

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from . import sessions as S
from .questions import QUESTIONS
from .service import NotesService, ServiceError

logger = logging.getLogger("notes.api")

TOKEN_COOKIE, SESSION_COOKIE = "nt_token", "nt_session"
MAX_FORM_BYTES = 8_192

SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
                               "frame-ancestors 'none'; base-uri 'none'",
    "X-Content-Type-Options": "nosniff",
    # "same-origin", not "no-referrer": with no-referrer a browser sends "Origin: null" on a
    # same-site form post, which the cross-site check below (rightly) refuses, so nobody could
    # sign in. Found by using the page in a real browser; the in-process test client hides it.
    "Referrer-Policy": "same-origin",
    "Cache-Control": "no-store",
}


# ------------------------------------------------------------------ request bodies

class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AskBody(_Body):
    request_id: str = Field(max_length=80)
    question: str = Field(max_length=2000)
    expected_session_version: int = Field(ge=1)


class AnswerBody(_Body):
    request_id: str = Field(max_length=80)
    answer: str = Field(max_length=2000)
    expected_session_version: int = Field(ge=1)


# ------------------------------------------------------------------- errors

class _Unauthorized(Exception):
    """No valid credential. Raised from a dependency so it happens BEFORE the body is validated:
    an unauthenticated caller must learn nothing about the request format."""


def _error(status: int, code: str, message: str, **extra: Any) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message, **extra}}, status_code=status)


def _log_unexpected(where: str, exc: BaseException) -> None:
    """Type and stack frames only. An exception message can hold a student's question or a
    private answer, so it is never written to a log."""
    frames = "".join(traceback.format_tb(exc.__traceback__)).rstrip()
    logger.error("unexpected error in %s: %s\n%s", where, type(exc).__name__, frames)


def _describe(exc: Exception) -> tuple[int, str, str, dict[str, Any]] | None:
    """(status, code, message, extra) for an error the client may be told about."""
    if isinstance(exc, S.NotFound):
        return 404, "not_found", "No such session.", {}
    if isinstance(exc, S.VersionConflict):
        return 409, "version_conflict", "The session changed. Reload it and try again.", \
            {"current_version": exc.actual}
    if isinstance(exc, S.RequestIdReused):
        return 409, "request_id_reused", "That request id was already used for different content.", {}
    if isinstance(exc, S.SessionBusy):
        return 409, "session_busy", "A request for this session is still running.", {}
    if isinstance(exc, ServiceError):
        status = {"not_available_offline": 422, "rate_limited": 429}.get(exc.code, 400)
        return status, exc.code, exc.message, {}
    return None


# --------------------------------------------------------------------- pages

_CSS = """
:root{color-scheme:light dark}
body{font:1.05rem/1.6 system-ui,-apple-system,Segoe UI,sans-serif;max-width:40rem;margin:0 auto;padding:1.5rem 1.2rem 4rem}
h1{font-size:1.5rem;margin:0 0 .3rem} h2{font-size:1.15rem;margin:1.6rem 0 .4rem}
.skip{position:absolute;left:-999px} .skip:focus{position:static;display:block;padding:.5rem}
.card{border:1px solid #71717a;border-radius:8px;padding:1rem 1.2rem;margin:1rem 0}
label{display:block;font-weight:600;margin:.8rem 0 .3rem}
input[type=text],input[type=password],textarea{width:100%;font:inherit;padding:.6rem;border:2px solid #52525b;border-radius:6px;background:transparent;color:inherit;box-sizing:border-box}
textarea{min-height:6rem}
button{font:inherit;font-weight:700;padding:.6rem 1.3rem;margin-top:.9rem;border:2px solid #0d5c5f;border-radius:6px;background:#0d5c5f;color:#fff;cursor:pointer}
button.secondary{background:transparent;color:inherit;border-color:#52525b}
:focus-visible{outline:3px solid #f59e0b;outline-offset:2px}
.alert{border:2px solid #b91c1c;border-radius:6px;padding:.7rem 1rem;margin:1rem 0}
.note{color:#52525b;font-size:.92rem} .sources{font-size:.95rem}
@media (prefers-color-scheme:dark){.note{color:#a1a1aa}}
"""


def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _page(title: str, body: str, status: int = 200) -> HTMLResponse:
    doc = (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
           f'<meta name="viewport" content="width=device-width,initial-scale=1">'
           f'<title>{_e(title)}</title><style>{_CSS}</style></head><body>'
           f'<a class="skip" href="#main">Skip to main content</a><main id="main">{body}</main></body></html>')
    return HTMLResponse(doc, status_code=status)


def _alert(message: str | None) -> str:
    return f'<p class="alert" role="alert">{_e(message)}</p>' if message else ""


def _login_page(mode: str, error: str | None = None, status: int = 200) -> HTMLResponse:
    return _page("Sign in - Notes tutor", f"""
<h1>Notes tutor</h1>
<p>Ask a question about your study notes. Answers come only from the notes, and each one is checked
against them before you see it.</p>
{_alert(error)}
<form method="post" action="/login">
<label for="token">Access code</label>
<input id="token" name="token" type="password" autocomplete="off" required autofocus>
<p class="note">Your tester gave you this code.</p>
<button type="submit">Start</button></form>""", status)


def _suggestions(mode: str) -> str:
    if mode != "scripted":
        return ""
    items = "".join(f"<li>{_e(q.text)}</li>" for q in QUESTIONS)
    return (f'<p class="note"><strong>Offline demonstration.</strong> Answers are pre-written for exactly '
            f'these questions, so ask one of them word for word:</p><ul class="note">{items}</ul>')


def _result_html(last: dict[str, Any] | None) -> str:
    if not last:
        return ""
    status = last.get("status")
    if status == "answered":
        sources = ", ".join(_e(s) for s in last.get("sources", []))
        out = (f'<section class="card" aria-labelledby="ans"><h2 id="ans">Answer</h2><p>{_e(last["answer"])}</p>'
               f'<p class="sources">From your notes: {sources}</p>')
        if last.get("explanation"):
            out += f'<h2>Explanation</h2><p>{_e(last["explanation"])}</p>'
        graded = last.get("graded")
        if graded:
            out += f'<h2>Your answer to the check question</h2><p>{_e(graded["feedback"])}</p>'
        return out + "</section>"
    if status == "not_in_notes":
        return (f'<section class="card" aria-labelledby="ans"><h2 id="ans">Not in your notes</h2>'
                f'<p>{_e(last["text"])}</p></section>')
    if status == "stopped":
        return (f'<section class="card" aria-labelledby="ans"><h2 id="ans">I could not finish</h2>'
                f'<p>{_e(last.get("text"))}</p></section>')
    return ""


def _ask_page(snap: dict[str, Any], mode: str, error: str | None = None, status: int = 200) -> HTMLResponse:
    version, request_id = snap["version"], uuid.uuid4().hex
    body = f"<h1>Notes tutor</h1>{_alert(error)}{_result_html(snap.get('last_result'))}"
    check = snap.get("check_question")
    if snap["waiting_for_answer"] and check:
        hidden = (f'<input type="hidden" name="request_id" value="{_e(request_id)}">'
                  f'<input type="hidden" name="expected_version" value="{version}">')
        if check["kind"] == "multiple_choice":
            # A radio group: a screen reader announces the question as the group's name and each
            # option as "1 of 3", and nobody has to guess the wording the grader expects.
            choices = "".join(
                f'<div><input type="radio" id="opt{i}" name="answer" value="{_e(o["option_id"])}" required>'
                f'<label for="opt{i}" style="display:inline;font-weight:400"> {_e(o["text"])}</label></div>'
                for i, o in enumerate(check["options"], start=1))
            field = f'<fieldset><legend>{_e(check["prompt"])}</legend>{choices}</fieldset>'
            prompt = ""
        else:
            field = '<label for="answer">Your answer</label><input id="answer" name="answer" type="text" required autofocus>'
            prompt = f'<p>{_e(check["prompt"])}</p>'
        body += (f'<section class="card" aria-labelledby="chk"><h2 id="chk">A question to check your understanding</h2>'
                 f'{prompt}<form method="post" action="/answer">{hidden}{field}'
                 f'<button type="submit">Check my answer</button></form></section>')
    else:
        body += (f'<form method="post" action="/ask"><input type="hidden" name="request_id" value="{_e(request_id)}">'
                 f'<input type="hidden" name="expected_version" value="{version}">'
                 f'<label for="question">Your question</label>'
                 f'<textarea id="question" name="question" required maxlength="500"></textarea>'
                 f'<button type="submit">Ask</button>'
                 f'<p class="note" id="wait">Each answer is checked against your notes before you see it, so it can '
                 f'take up to half a minute. This page shows nothing new until it is ready. Please do not press Ask '
                 f'again.</p></form>{_suggestions(mode)}')
    body += ('<form method="post" action="/new"><button class="secondary" type="submit">Start a new session</button></form>'
             '<p><a href="/sources">Your notes</a></p><form method="post" action="/logout"><button class="secondary" type="submit">Sign out</button></form>')
    return _page("Notes tutor", body, status)


# ------------------------------------------------------------------- the app

def create_app(service: NotesService) -> FastAPI:
    app = FastAPI(title="Notes tutor", docs_url=None, redoc_url=None, openapi_url=None)
    mode = getattr(service.provider, "mode", "scripted")

    @app.middleware("http")
    async def headers(request: Request, call_next):
        response = await call_next(request)
        for key, value in SECURITY_HEADERS.items():
            response.headers[key] = value
        return response

    @app.exception_handler(_Unauthorized)
    async def unauthorized(request: Request, exc: _Unauthorized):
        return _error(401, "unauthorized", "A valid access token is required.")

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, exc: RequestValidationError):
        return _error(400, "invalid_request", "The request body is not valid.")   # never echoes the input

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception):
        _log_unexpected(request.url.path, exc)
        return _error(500, "internal_error", "Something went wrong on our side.")

    def account_of(request: Request) -> str | None:
        header = request.headers.get("authorization", "")
        token = header[7:].strip() if header.lower().startswith("bearer ") else request.cookies.get(TOKEN_COOKIE)
        return service.authenticate(token)

    def require_account(request: Request) -> str:
        account = account_of(request)
        if not account:
            raise _Unauthorized()
        return account

    def guarded(fn):
        """Run a service call and turn its known errors into typed responses."""
        try:
            return fn()
        except Exception as exc:                          # noqa: BLE001
            described = _describe(exc)
            if described is None:
                raise
            status, code, message, extra = described
            return _error(status, code, message, **extra)

    # -- JSON API -----------------------------------------------------------

    @app.get("/health/live")
    def live():
        return {"status": "ok", "mode": mode}

    @app.post("/v1/sessions")
    def new_session(account: str = Depends(require_account)):
        view = service.create_session(account)
        return JSONResponse({"session_id": view.session_id, "version": view.version}, status_code=201)

    @app.get("/v1/sessions/{session_id}")
    def get_session(session_id: str, account: str = Depends(require_account)):
        return guarded(lambda: service.snapshot(account, session_id))

    @app.post("/v1/sessions/{session_id}/turns")
    def turn(session_id: str, body: AskBody, account: str = Depends(require_account)):
        def run():
            result, snap, replayed = service.ask(account, session_id, body.request_id, body.question,
                                                 body.expected_session_version)
            return {"result": result, "session": snap, "replayed": replayed}
        return guarded(run)

    @app.post("/v1/sessions/{session_id}/answers")
    def answer(session_id: str, body: AnswerBody, account: str = Depends(require_account)):
        def run():
            result, snap, replayed = service.answer_check(account, session_id, body.request_id, body.answer,
                                                          body.expected_session_version)
            return {"result": result, "session": snap, "replayed": replayed}
        return guarded(run)

    @app.post("/v1/sessions/{session_id}/cancel")
    def cancel(session_id: str, account: str = Depends(require_account)):
        return guarded(lambda: {"cancelled": service.cancel(account, session_id)})

    # -- the tester page ------------------------------------------------------

    async def form(request: Request) -> dict[str, str] | None:
        """A small urlencoded form, size-limited. Returns None if it is too large."""
        raw = await request.body()
        if len(raw) > MAX_FORM_BYTES:
            return None
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") != f"{request.url.scheme}://{request.headers.get('host', '')}":
            return None                                   # a cross-site post: refuse
        return {k: v[0] for k, v in parse_qs(raw.decode("utf-8", "replace"), keep_blank_values=True).items()}

    def current(request: Request) -> tuple[str, str] | None:
        account, session_id = account_of(request), request.cookies.get(SESSION_COOKIE)
        return (account, session_id) if account and session_id else None

    def cookies(response, request: Request, token: str | None, session_id: str | None) -> None:
        secure = request.url.scheme == "https"
        for name, value in ((TOKEN_COOKIE, token), (SESSION_COOKIE, session_id)):
            if value is None:
                response.delete_cookie(name, path="/")
            else:
                response.set_cookie(name, value, httponly=True, samesite="strict", secure=secure, path="/")

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        return RedirectResponse("/ask", status_code=303) if current(request) else _login_page(mode)

    @app.post("/login")
    async def login(request: Request):
        data = await form(request)
        if data is None:
            return _login_page(mode, "That request was refused.", 400)
        token = data.get("token", "").strip()
        account = await run_in_threadpool(service.authenticate, token)
        if account is None:
            return _login_page(mode, "That access code was not recognised.", 401)
        view = await run_in_threadpool(service.create_session, account)
        response = RedirectResponse("/ask", status_code=303)
        cookies(response, request, token, view.session_id)
        return response

    @app.get("/ask", response_class=HTMLResponse)
    def ask_page(request: Request):
        who = current(request)
        if who is None:
            return RedirectResponse("/", status_code=303)
        try:
            return _ask_page(service.snapshot(*who), mode)
        except S.NotFound:
            return RedirectResponse("/", status_code=303)

    async def act(request: Request, name: str, call):
        who = current(request)
        if who is None:
            return RedirectResponse("/", status_code=303)
        data = await form(request)
        if data is None:
            return _ask_page(await run_in_threadpool(service.snapshot, *who), mode, "That request was refused.", 400)
        try:
            await run_in_threadpool(call, *who, data)
        except Exception as exc:                          # noqa: BLE001
            described = _describe(exc)
            if described is None:
                raise
            status, _, message, _ = described
            return _ask_page(await run_in_threadpool(service.snapshot, *who), mode, message, status)
        return RedirectResponse("/ask", status_code=303)

    @app.post("/ask")
    async def ask(request: Request):
        def call(account, session_id, data):
            return service.ask(account, session_id, data.get("request_id", ""), data.get("question", ""),
                               _int(data.get("expected_version")))
        return await act(request, "ask", call)

    @app.post("/answer")
    async def answer_form(request: Request):
        def call(account, session_id, data):
            return service.answer_check(account, session_id, data.get("request_id", ""), data.get("answer", ""),
                                        _int(data.get("expected_version")))
        return await act(request, "answer", call)

    @app.post("/new")
    async def new(request: Request):
        who = current(request)
        if who is None or await form(request) is None:
            return RedirectResponse("/", status_code=303)
        view = await run_in_threadpool(service.create_session, who[0])
        response = RedirectResponse("/ask", status_code=303)
        cookies(response, request, request.cookies.get(TOKEN_COOKIE), view.session_id)
        return response

    def _sources_page(who, error: str | None = None, status: int = 200) -> HTMLResponse:
        import uuid
        rows = "".join(
            f'<li>{_e(x["name"])}: {_e(x["state"])}' + (f' ({_e(x["error"])})' if x.get("error") else "")
            + ("" if x["builtin"] else
               f'<form method="post" action="/sources/delete"><input type="hidden" name="source_id" '
               f'value="{_e(x["source_id"])}"><button type="submit">Delete {_e(x["name"])}</button></form>')
            + "</li>" for x in service.list_sources(who[0]))
        body = (f'<h1>Your notes</h1>{_alert(error)}<ul>{rows}</ul>'
                + ('<form method="post" action="/sources"><h2>Add notes</h2>'
                   '<label for="name">Name</label><input type="text" id="name" name="name" maxlength="80" required>'
                   '<label for="text">Paste the text (Markdown or plain)</label>'
                   '<textarea id="text" name="text" rows="12" required></textarea>'
                   f'<input type="hidden" name="upload_key" value="{uuid.uuid4().hex}">'
                   '<button type="submit">Upload</button></form>' if mode == "live" else
                   '<p>Uploads need the live mode.</p>')
                + '<p><a href="/ask">Back to questions</a></p>')
        return _page("Your notes", body, status)

    @app.get("/sources", response_class=HTMLResponse)
    def sources_page(request: Request):
        who = current(request)
        return _sources_page(who) if who else RedirectResponse("/", status_code=303)

    async def source_act(request: Request, call):
        who = current(request)
        if who is None:
            return RedirectResponse("/", status_code=303)
        data = await form(request)
        if data is None:
            return _sources_page(who, "That request was refused.", 400)
        try:
            await run_in_threadpool(call, who[0], data)
        except Exception as exc:                          # noqa: BLE001
            described = _describe(exc)
            if described is None:
                raise
            return _sources_page(who, described[2], described[0])
        return RedirectResponse("/sources", status_code=303)

    @app.post("/sources")
    async def upload(request: Request):
        return await source_act(request, lambda account, d: service.upload_source(
            account, d.get("name", ""), d.get("name", "notes") + ".md", d.get("text", ""), d.get("upload_key")))

    @app.post("/sources/delete")
    async def delete_source(request: Request):
        return await source_act(request, lambda account, d: service.delete_source(account, d.get("source_id", "")))

    @app.post("/logout")
    async def logout(request: Request):
        response = RedirectResponse("/", status_code=303)
        cookies(response, request, None, None)
        return response

    return app


def _int(value: str | None) -> int:
    try:
        return int(value or "")
    except ValueError:
        return 0
