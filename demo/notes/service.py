"""The notes service: one session-safe entry point for asking a question and answering its check.

Sits between the API/web layer and everything else: it checks nothing about HTTP, and it
never lets HTTP-shaped concerns leak into the flows. Each public method is authorized by
account, idempotent by request id, and version-checked, all through demo/notes/sessions.py.

Which models answer is a provider's business (`LiveProvider` for OpenRouter, `ScriptedProvider`
for the offline demo and tests), so nothing here changes between them.

The Tutor never takes an answer away: it runs after the notes run has completed, and a Tutor
that fails leaves the answer in place, just without the explanation and check question.
"""
from __future__ import annotations

import time
from typing import Any

from slice import callback, runner
from slice.records import RunState
from slice.store import Store

from . import canned, sessions as S
from . import sources as SRC
from . import tutor as tutor_mod
from .flow import build_flow
from .questions import QUESTIONS
from .schema import AnswerDraft

MAX_REQUESTS_PER_HOUR = 60
"""Requests one account may make per hour. Live, every question spends the team's shared, capped
key, so a leaked or careless access code must not be able to drain it. Replays cost nothing and
are always answered; a request refused for this reason has not run, so it does not count."""

MAX_QUESTION_CHARS = 500
MAX_ANSWER_CHARS = 300


class ServiceError(Exception):
    """A request the service refuses. `code` is safe to show a client."""

    def __init__(self, code: str, message: str) -> None:
        self.code, self.message = code, message
        super().__init__(message)


class NoScript(ServiceError):
    def __init__(self) -> None:
        super().__init__("not_available_offline",
                         "This offline demonstration only knows the six fixed questions.")


# ---------------------------------------------------------------- providers

class LiveProvider:
    """The real models, through the kit's OpenRouter client. Spends the team key."""

    def __init__(self, settings, *, gate: str = "escalation", allow_fallback: bool = False) -> None:
        from .validate import live_settings
        self.settings = live_settings(settings, allow_fallback=allow_fallback)
        self.gate_model = self.settings.escalation_model if gate == "escalation" else None
        self.mode = "live"

    def prepare(self, store: Store) -> None:
        from .corpus import ingest_notes
        ingest_notes(store)
        SRC.register_builtin(store.db)

    def for_question(self, text: str):
        from slice.llm import complete
        return {"call": complete, "search": None, "tutor_call": complete}


class ScriptedProvider:
    """Hand-written replies for the six fixed questions. Not a model: an offline demonstration."""

    def __init__(self, settings) -> None:
        self.settings, self.gate_model, self.mode = settings, None, "scripted"

    def prepare(self, store: Store) -> None:
        return None

    def for_question(self, text: str):
        match = next((q for q in QUESTIONS if q.text == text.strip()), None)
        if match is None:
            raise NoScript()
        model, search = canned.scripted(match)
        tutor = canned.scripted_tutor(match.id) if match.outcome == "answer" else None
        return {"call": model, "search": search, "tutor_call": tutor}


# ------------------------------------------------------------------ service

class NotesService:
    def __init__(self, db_path: str, provider, *, tutor: bool = True,
                 max_requests_per_hour: int = MAX_REQUESTS_PER_HOUR, tracer=None) -> None:
        self.tracer = tracer
        self.db_path, self.provider, self.tutor = str(db_path), provider, tutor
        self.max_requests_per_hour = max_requests_per_hour
        store = Store(self.db_path)
        S.migrate(store.db)
        provider.prepare(store)

    def _open(self) -> Store:
        # The kit's connections belong to the thread that made them, so each call opens its own.
        return Store(self.db_path)

    # -- identity ---------------------------------------------------------

    def authenticate(self, token: str | None) -> str | None:
        return S.authenticate(self._open().db, token)

    def create_session(self, account_id: str) -> S.SessionView:
        return S.create_session(self._open().db, account_id)

    def snapshot(self, account_id: str, session_id: str) -> dict[str, Any]:
        view = S.get_session(self._open().db, account_id, session_id)
        return self._snapshot(view)

    @staticmethod
    def _snapshot(view: S.SessionView) -> dict[str, Any]:
        pending = view.state.get("pending_check")
        return {"session_id": view.session_id, "version": view.version,
                "turns": view.state.get("turns", 0),
                "waiting_for_answer": pending is not None,
                "check_question": pending["public"] if pending else None,
                "last_result": view.state.get("last_result")}

    def cancel(self, account_id: str, session_id: str) -> bool:
        return S.request_cancel(self._open().db, account_id, session_id)

    # -- sources (live mode only: uploads need the worker and the real embeddings) -----

    def _require_live(self) -> None:
        if self.provider.mode != "live":
            raise ServiceError("uploads_unavailable", "Uploads need the live mode.")

    def upload_source(self, account_id: str, name: str, filename: str, text: str,
                      upload_key: str | None = None) -> dict[str, Any]:
        self._require_live()
        try:
            return SRC.create_source(self._open().db, account_id, name, filename, text, upload_key=upload_key)
        except SRC.SourceError as e:
            raise ServiceError(e.code, str(e)) from e

    def list_sources(self, account_id: str) -> list[dict[str, Any]]:
        return SRC.list_sources(self._open().db, account_id)

    def delete_source(self, account_id: str, source_id: str) -> None:
        self._require_live()
        SRC.delete_source(self._open().db, account_id, source_id)

    def make_worker(self):
        """The worker that ingests and purges sources. It opens its own connections."""
        from . import jobs as J
        handlers = {SRC.INGEST_JOB: SRC.make_ingest_handler(self._open),
                    SRC.PURGE_JOB: SRC.make_purge_handler(self._open)}
        return J.Worker(lambda: self._open().db, handlers, worker_id="serve-worker")

    # -- asking -----------------------------------------------------------

    def ask(self, account_id: str, session_id: str, request_id: str, question: str,
            expected_version: int) -> tuple[dict[str, Any], dict[str, Any], bool]:
        question = (question or "").strip()
        if not question:
            raise ServiceError("empty_question", "Type a question first.")
        if len(question) > MAX_QUESTION_CHARS:
            raise ServiceError("question_too_long", f"Keep the question under {MAX_QUESTION_CHARS} characters.")
        _validate_request_id(request_id)

        db = self._open().db

        def work(session: S.SessionView):
            return self._answer_question(db, session, request_id, question)

        result, view, replayed = S.handle_request(
            db, account_id, session_id, request_id, S.fingerprint("ask", question), expected_version, work)
        if self.tracer is not None and not replayed:
            self._trace(view)
        return result, self._snapshot(view), replayed

    def _trace(self, view: S.SessionView) -> None:
        """Queue this turn's runs for export. Queuing cannot fail a request."""
        try:
            self.tracer.submit(view.state["last_run"])
            pending = view.state.get("pending_check")
            if pending:
                self.tracer.submit(pending["run_id"])
        except Exception:                                # noqa: BLE001
            pass

    def _check_rate(self, db, session: S.SessionView) -> None:
        account = db.execute("SELECT account_id FROM sessions WHERE session_id=?", (session.session_id,)).fetchone()[0]
        used = db.execute(
            "SELECT COUNT(*) FROM session_requests r JOIN sessions s ON s.session_id = r.session_id "
            "WHERE s.account_id=? AND r.created_at > ? AND r.status != 'failed'",
            (account, time.time() - 3600)).fetchone()[0]
        if used > self.max_requests_per_hour:        # this request's own row is already counted
            raise ServiceError("rate_limited", "That is a lot of requests. Please wait a while and try again.")

    def _answer_question(self, db, session: S.SessionView, request_id: str, question: str):
        self._check_rate(db, session)
        parts = self.provider.for_question(question)
        store = self._open()
        stop = lambda: S.is_cancelled(db, session.session_id, request_id)   # noqa: E731
        run_id = store.create_run("notes")
        store.append(run_id, "input", {"text": question}, produced_by="system")
        flow_args: dict[str, Any] = {"call": parts["call"], "gate_model": self.provider.gate_model,
                                     "should_stop": stop}
        if parts["search"] is not None:
            flow_args["search"] = parts["search"]
        elif self.provider.mode == "live":
            # Pin the sources this session sees the first time it asks, then search only those
            # passages. A newer upload never changes a running session; a deleted source vanishes.
            account = db.execute("SELECT account_id FROM sessions WHERE session_id=?",
                                 (session.session_id,)).fetchone()[0]
            pinned = session.state.get("source_versions")
            if pinned is None:
                pinned = SRC.active_version_ids(db, account)
            allowed = SRC.allowed_chunk_ids(db, account, pinned)
            session.state["source_versions"] = pinned
            from .corpus import search_notes
            flow_args["search"] = lambda st, q, k: search_notes(st, q, k=k, allowed=allowed)
        final = runner.advance(store, run_id, build_flow(**flow_args), self.provider.settings)

        state = dict(session.state)
        state.update(last_run=run_id, turns=state.get("turns", 0) + 1, pending_check=None)

        if final is RunState.FAILED:
            failure = store.latest(run_id, "failure") or {}
            if failure.get("kind") == "cancelled":
                return state, {"status": "cancelled"}
            stopped = {"status": "stopped", "reason": failure.get("kind"), "text": failure.get("reply", "")}
            state["last_result"] = stopped
            return state, stopped

        draft = AnswerDraft.model_validate(store.latest(run_id, "draft"))
        if draft.action == "state_gap":
            gap = {"status": "not_in_notes", "text": draft.text}
            state["last_result"] = gap
            return state, gap

        passages = {p["evidence_id"]: p for p in store.latest(run_id, "evidence")["passages"]}
        result: dict[str, Any] = {
            "status": "answered", "answer": draft.text,
            "sources": [f'{passages[e]["doc"]}#{passages[e]["ordinal"]}' for e in draft.cited_evidence_ids],
            "explanation": None, "check_question": None}

        if self.tutor and parts["tutor_call"] is not None:
            tutor_run = tutor_mod.start_tutor_run(store, run_id)
            tutor_state = runner.advance(store, tutor_run, tutor_mod.build_flow(
                call=parts["tutor_call"], should_stop=stop), self.provider.settings)
            if tutor_state is not RunState.FAILED:
                view = tutor_mod.public_view(store, tutor_run)
                result["explanation"] = view["explanation"]
                if view["question"]:
                    result["check_question"] = view["question"]
                    state["pending_check"] = {"run_id": tutor_run, "question_id": view["question"]["id"],
                                              "public": view["question"]}
        state["last_result"] = result
        return state, result

    # -- answering the check question --------------------------------------

    def answer_check(self, account_id: str, session_id: str, request_id: str, answer: str,
                     expected_version: int) -> tuple[dict[str, Any], dict[str, Any], bool]:
        answer = (answer or "").strip()
        if not answer:
            raise ServiceError("empty_answer", "Type an answer first.")
        if len(answer) > MAX_ANSWER_CHARS:
            raise ServiceError("answer_too_long", f"Keep the answer under {MAX_ANSWER_CHARS} characters.")
        _validate_request_id(request_id)

        db = self._open().db

        def work(session: S.SessionView):
            pending = session.state.get("pending_check")
            if pending is None:
                raise ServiceError("no_question_waiting", "There is no check question waiting for an answer.")
            store = self._open()
            callback.answer(store, pending["question_id"], answer, who="student")
            runner.advance(store, pending["run_id"], tutor_mod.build_flow(call=None), self.provider.settings)
            attempt = store.latest(pending["run_id"], "attempt") or {}
            feedback = tutor_mod.public_view(store, pending["run_id"])["feedback"]
            state = dict(session.state)
            state["pending_check"] = None
            graded = {"status": "graded", "correct": bool(attempt.get("correct")), "feedback": feedback}
            # The answer stays on screen with its outcome; the question it asked is done.
            state["last_result"] = {**(state.get("last_result") or {}), "check_question": None, "graded": graded}
            return state, graded

        result, view, replayed = S.handle_request(
            db, account_id, session_id, request_id, S.fingerprint("answer", answer), expected_version, work)
        return result, self._snapshot(view), replayed


def _validate_request_id(request_id: str) -> None:
    if not request_id or len(request_id) > 80 or not all(c.isalnum() or c in "-_" for c in request_id):
        raise ServiceError("bad_request_id", "request_id must be 1 to 80 letters, digits, - or _.")
