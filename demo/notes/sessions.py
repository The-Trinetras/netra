"""Accounts, credentials, sessions and idempotent requests, on the kit's SQLite database.

Ported from the earlier project's identity, session and persistence layers, scaled to this
slice. What is kept, because it is what makes a session safe rather than merely stored:

  * Authorization is checked at the boundary, not assumed from an id. A session id alone is
    not authority: the caller's account must own the session, and anything else looks like
    "not found", so ids cannot be probed.
  * Credentials are stored as hashes. The plaintext token is shown once, when it is issued.
  * A session has a version that only ever goes up, and a request states the version it was
    made against. A stale request is refused (conflict), never silently applied.
  * Requests are idempotent by request id. Retrying the same request returns the recorded
    result and does not advance the version; reusing a request id for DIFFERENT content is
    an error, not a replay.
  * No database transaction is held open across a model call. A request is claimed, its work
    runs, and only then is the result committed, after re-checking the version. A request that
    was cancelled while it ran commits nothing.
  * Migrations are forward-only and checksummed: an applied migration that has been edited
    is an error, because the fix for a bad migration is a new migration, never a rewrite.

What is NOT ported: PostgreSQL, device records, the WebSocket protocol and reconnect, and
per-device credentials. This is one process on one SQLite file.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable

# -------------------------------------------------------------------- errors


class SessionError(Exception):
    """Base for everything a caller can be told about a session."""


class NotFound(SessionError):
    """No such session for this account. Also what another account's session looks like."""


class VersionConflict(SessionError):
    def __init__(self, expected: int, actual: int) -> None:
        self.expected, self.actual = expected, actual
        super().__init__(f"session is at version {actual}, the request was made against {expected}")


class RequestIdReused(SessionError):
    """The same request id arrived with different content. Not a retry, so not replayed."""


class SessionBusy(SessionError):
    """A request for this session is already running. One at a time: a retry of the running
    request waits for its result instead of starting a second one."""


class MigrationTampered(RuntimeError):
    """An already-applied migration no longer matches what was applied."""


# --------------------------------------------------------------- migrations

MIGRATIONS: list[tuple[str, str]] = [
    ("0001_accounts_credentials_sessions", """
        CREATE TABLE accounts (
            account_id TEXT PRIMARY KEY,
            name       TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE TABLE credentials (
            token_sha256 TEXT PRIMARY KEY,
            account_id   TEXT NOT NULL REFERENCES accounts(account_id),
            created_at   REAL NOT NULL,
            revoked_at   REAL
        );
        CREATE TABLE sessions (
            session_id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL REFERENCES accounts(account_id),
            version    INTEGER NOT NULL,
            state_json TEXT NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE INDEX sessions_by_account ON sessions(account_id);
    """),
    ("0002_session_requests", """
        CREATE TABLE session_requests (
            session_id       TEXT NOT NULL REFERENCES sessions(session_id),
            request_id       TEXT NOT NULL,
            fingerprint      TEXT NOT NULL,
            status           TEXT NOT NULL,          -- running | done | cancelled | failed
            result_json      TEXT,
            cancel_requested INTEGER NOT NULL DEFAULT 0,
            created_at       REAL NOT NULL,
            PRIMARY KEY (session_id, request_id)
        );
    """),
]


from .jobs import JOBS_MIGRATION  # noqa: E402  (the queue's table lives with the queue)

MIGRATIONS.append(JOBS_MIGRATION)

from .sources import SOURCES_MIGRATION  # noqa: E402

MIGRATIONS.append(SOURCES_MIGRATION)


def _checksum(sql: str) -> str:
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


def migrate(db: sqlite3.Connection, migrations: list[tuple[str, str]] | None = None) -> list[str]:
    """Apply migrations not yet applied, in order. Returns the names applied now.

    An applied migration whose text has changed raises MigrationTampered and applies nothing
    further: it means the database and the code disagree about what was run.
    """
    migrations = MIGRATIONS if migrations is None else migrations
    db.execute("CREATE TABLE IF NOT EXISTS schema_migrations "
               "(name TEXT PRIMARY KEY, checksum TEXT NOT NULL, applied_at REAL NOT NULL)")
    applied = {r[0]: r[1] for r in db.execute("SELECT name, checksum FROM schema_migrations")}
    for name, sql in migrations:
        if name in applied and applied[name] != _checksum(sql):
            raise MigrationTampered(
                f"migration {name} was applied with different content. Do not edit an applied "
                "migration; add a new one.")
    done = []
    for name, sql in migrations:
        if name in applied:
            continue
        db.execute("BEGIN IMMEDIATE")
        try:
            for statement in [s for s in sql.split(";") if s.strip()]:
                db.execute(statement)
            db.execute("INSERT INTO schema_migrations(name, checksum, applied_at) VALUES (?,?,?)",
                       (name, _checksum(sql), time.time()))
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
        done.append(name)
    return done


# ---------------------------------------------------------------- identity

def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_account(db: sqlite3.Connection, name: str) -> str:
    account_id = f"acct_{uuid.uuid4().hex[:12]}"
    db.execute("INSERT INTO accounts(account_id, name, created_at) VALUES (?,?,?)",
               (account_id, name, time.time()))
    return account_id


def issue_token(db: sqlite3.Connection, account_id: str) -> str:
    """Issue a credential. The plaintext is returned once and never stored: only its hash is."""
    token = "nt_" + secrets.token_urlsafe(24)
    db.execute("INSERT INTO credentials(token_sha256, account_id, created_at) VALUES (?,?,?)",
               (_token_hash(token), account_id, time.time()))
    return token


def revoke_token(db: sqlite3.Connection, token: str) -> None:
    db.execute("UPDATE credentials SET revoked_at=? WHERE token_sha256=? AND revoked_at IS NULL",
               (time.time(), _token_hash(token)))


def authenticate(db: sqlite3.Connection, token: str | None) -> str | None:
    """The account a token belongs to, or None. A revoked or unknown token is None."""
    if not token:
        return None
    row = db.execute("SELECT account_id FROM credentials WHERE token_sha256=? AND revoked_at IS NULL",
                     (_token_hash(token),)).fetchone()
    return row[0] if row else None


# ---------------------------------------------------------------- sessions

@dataclass(frozen=True)
class SessionView:
    session_id: str
    version: int
    state: dict[str, Any]


def create_session(db: sqlite3.Connection, account_id: str) -> SessionView:
    session_id, now = f"sess_{uuid.uuid4().hex[:12]}", time.time()
    state: dict[str, Any] = {}
    db.execute("INSERT INTO sessions(session_id, account_id, version, state_json, created_at, updated_at) "
               "VALUES (?,?,?,?,?,?)", (session_id, account_id, 1, json.dumps(state), now, now))
    return SessionView(session_id, 1, state)


def get_session(db: sqlite3.Connection, account_id: str, session_id: str) -> SessionView:
    """The session if this account owns it. Anyone else's session is NotFound, not Forbidden:
    a different answer would confirm the id exists."""
    row = db.execute("SELECT version, state_json FROM sessions WHERE session_id=? AND account_id=?",
                     (session_id, account_id)).fetchone()
    if row is None:
        raise NotFound(session_id)
    return SessionView(session_id, row[0], json.loads(row[1]))


def fingerprint(kind: str, content: str) -> str:
    return hashlib.sha256(f"{kind}\n{content}".encode("utf-8")).hexdigest()


def handle_request(
    db: sqlite3.Connection, account_id: str, session_id: str, request_id: str, fp: str,
    expected_version: int, work: Callable[[SessionView], tuple[dict[str, Any], dict[str, Any]]],
) -> tuple[dict[str, Any], SessionView, bool]:
    """Run `work` once for this request id. Returns (result, session after, replayed).

    `work(session)` returns (new_state, result) and may take a long time: it runs with NO
    transaction open. Only the claim and the commit are transactions.
    """
    session = get_session(db, account_id, session_id)

    # 1. Claim, or replay. One short transaction.
    db.execute("BEGIN IMMEDIATE")
    try:
        row = db.execute("SELECT fingerprint, status, result_json FROM session_requests "
                         "WHERE session_id=? AND request_id=?", (session_id, request_id)).fetchone()
        if row is not None:
            if row[0] != fp:
                raise RequestIdReused(request_id)
            if row[1] == "running":
                raise SessionBusy(request_id)
            if row[1] in ("done", "cancelled"):
                db.execute("COMMIT")
                current = get_session(db, account_id, session_id)
                return json.loads(row[2]), current, True
            # status "failed": that attempt had no effect, so the same request may run again.
        if session.version != expected_version:
            raise VersionConflict(expected_version, session.version)
        running = db.execute("SELECT 1 FROM session_requests WHERE session_id=? AND status='running'",
                             (session_id,)).fetchone()
        if running:
            raise SessionBusy(request_id)
        if row is None:
            db.execute("INSERT INTO session_requests(session_id, request_id, fingerprint, status, created_at) "
                       "VALUES (?,?,?,?,?)", (session_id, request_id, fp, "running", time.time()))
        else:
            db.execute("UPDATE session_requests SET status='running', result_json=NULL, cancel_requested=0 "
                       "WHERE session_id=? AND request_id=?", (session_id, request_id))
        db.execute("COMMIT")
    except BaseException:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise

    # 2. The work, outside any transaction.
    try:
        new_state, result = work(session)
    except BaseException:
        db.execute("UPDATE session_requests SET status='failed', result_json=? "
                   "WHERE session_id=? AND request_id=?",
                   (json.dumps({"status": "failed"}), session_id, request_id))
        raise

    # 3. Commit, after re-checking that nothing changed underneath and that nobody cancelled.
    db.execute("BEGIN IMMEDIATE")
    try:
        cancelled = db.execute("SELECT cancel_requested FROM session_requests "
                               "WHERE session_id=? AND request_id=?", (session_id, request_id)).fetchone()[0]
        if cancelled:
            result = {"status": "cancelled"}
            db.execute("UPDATE session_requests SET status='cancelled', result_json=? "
                       "WHERE session_id=? AND request_id=?", (json.dumps(result), session_id, request_id))
            db.execute("COMMIT")
            return result, get_session(db, account_id, session_id), False
        version = db.execute("SELECT version FROM sessions WHERE session_id=?", (session_id,)).fetchone()[0]
        if version != session.version:
            raise VersionConflict(session.version, version)
        db.execute("UPDATE sessions SET version=version+1, state_json=?, updated_at=? WHERE session_id=?",
                   (json.dumps(new_state), time.time(), session_id))
        db.execute("UPDATE session_requests SET status='done', result_json=? "
                   "WHERE session_id=? AND request_id=?", (json.dumps(result), session_id, request_id))
        db.execute("COMMIT")
    except BaseException:
        if db.in_transaction:
            db.execute("ROLLBACK")
        db.execute("UPDATE session_requests SET status='failed', result_json=? "
                   "WHERE session_id=? AND request_id=?",
                   (json.dumps({"status": "failed"}), session_id, request_id))
        raise
    return result, get_session(db, account_id, session_id), False


def request_cancel(db: sqlite3.Connection, account_id: str, session_id: str) -> bool:
    """Ask the running request of this session to stop. Returns whether one was running.
    Ownership is checked like everything else."""
    get_session(db, account_id, session_id)
    cur = db.execute("UPDATE session_requests SET cancel_requested=1 "
                     "WHERE session_id=? AND status='running'", (session_id,))
    return cur.rowcount > 0


def is_cancelled(db: sqlite3.Connection, session_id: str, request_id: str) -> bool:
    row = db.execute("SELECT cancel_requested FROM session_requests WHERE session_id=? AND request_id=?",
                     (session_id, request_id)).fetchone()
    return bool(row and row[0])
