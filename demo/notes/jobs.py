"""Durable background jobs on the kit's SQLite database: leases, fenced writes, retries.

Ported from the earlier project's worker runtime (job repository, leases, dispatcher and
backoff), scaled to one SQLite file. What is kept, because it is what makes background work
safe rather than merely asynchronous:

  * At-least-once execution. A job can run more than once (a crashed worker, an expired lease),
    so handlers must be idempotent; nothing here pretends otherwise.
  * Leases. A claim gives one worker exclusive, time-bounded ownership. The attempt is counted at
    CLAIM time, so a worker that crashed still used one up, and a job that keeps crashing workers
    is dead-lettered instead of being claimed forever.
  * Fenced writes. Completing, failing, cancelling, renewing and recording a stage all require the
    lease token, the worker id and an unexpired lease. A worker that lost its lease cannot write.
  * Checkpointed stages and remote operation ids. A retry skips stages already done, and a provider
    id recorded before a crash lets the retry reconcile instead of calling the provider again.
  * Bounded retry with exponential backoff and jitter; permanent errors and cancellation are terminal.
  * No transaction is held open while a handler runs. The claim and each write are short.
  * The stored error is the exception TYPE only, never its message, which can carry private text.

Not ported: PostgreSQL row locking (SKIP LOCKED), the transactional outbox and the tracing spans.
SQLite serializes writers, so the claim uses BEGIN IMMEDIATE instead.
"""
from __future__ import annotations

import json
import random
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import ValidationError

# -------------------------------------------------------------------- errors


class LeaseLostError(RuntimeError):
    """This worker no longer owns the job. It must write nothing further."""


class PermanentJobError(RuntimeError):
    """The job can never succeed (bad input). Dead-lettered, never retried."""


class JobCancelled(RuntimeError):
    """The job was deliberately stopped. A terminal outcome, neither a failure nor a dead letter."""


# ---------------------------------------------------------------- migration

JOBS_MIGRATION = ("0003_jobs", """
    CREATE TABLE jobs (
        job_id                TEXT PRIMARY KEY,
        job_type              TEXT NOT NULL,
        idempotency_key       TEXT NOT NULL,
        payload_json          TEXT NOT NULL,
        status                TEXT NOT NULL,      -- pending | leased | completed | dead_letter | cancelled
        attempts              INTEGER NOT NULL DEFAULT 0,
        max_attempts          INTEGER NOT NULL,
        next_run_at           REAL NOT NULL,
        lease_token           TEXT,
        worker_id             TEXT,
        lease_until           REAL,
        completed_stages_json TEXT NOT NULL DEFAULT '[]',
        remote_ops_json       TEXT NOT NULL DEFAULT '{}',
        last_error            TEXT,
        created_at            REAL NOT NULL,
        updated_at            REAL NOT NULL,
        UNIQUE (job_type, idempotency_key)
    );
    CREATE INDEX jobs_claimable ON jobs(status, next_run_at)
""")


@dataclass
class Job:
    job_id: str
    job_type: str
    payload: dict[str, Any]
    attempts: int
    max_attempts: int
    lease_token: str
    worker_id: str
    lease_until: float
    completed_stages: list[str] = field(default_factory=list)
    remote_ops: dict[str, str] = field(default_factory=dict)


# ------------------------------------------------------------------ backoff

class ExponentialBackoff:
    """delay = min(max_delay, base * 2**attempts), then +/- jitter. Jitter keeps a crowd of
    failing jobs from all retrying at the same instant."""

    def __init__(self, base: float = 1.0, max_delay: float = 900.0, jitter: float = 0.2,
                 rng: Callable[[], float] = random.random) -> None:
        if not 0.0 <= jitter <= 1.0:
            raise ValueError("jitter must be between 0 and 1")
        self.base, self.max_delay, self.jitter, self._rng = base, max_delay, jitter, rng

    def next_delay(self, attempts: int) -> float:
        if attempts < 0:
            raise ValueError("attempts must be >= 0")
        capped = min(self.max_delay, self.base * (2 ** min(attempts, 32)))
        spread = capped * self.jitter
        return max(0.0, capped + (self._rng() * 2 - 1) * spread)


# ----------------------------------------------------------------- the queue

def enqueue(db: sqlite3.Connection, job_type: str, idempotency_key: str, payload: dict[str, Any],
            max_attempts: int = 5, now: float | None = None) -> tuple[str, bool]:
    """Add a job. Enqueueing the same (type, idempotency key) again returns the existing job and
    does not create a second one. Returns (job_id, created)."""
    now = time.time() if now is None else now
    job_id = f"job_{uuid.uuid4().hex[:12]}"
    cur = db.execute(
        "INSERT OR IGNORE INTO jobs(job_id, job_type, idempotency_key, payload_json, status, max_attempts, "
        "next_run_at, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (job_id, job_type, idempotency_key, json.dumps(payload), "pending", max_attempts, now, now, now))
    if cur.rowcount:
        return job_id, True
    row = db.execute("SELECT job_id FROM jobs WHERE job_type=? AND idempotency_key=?",
                     (job_type, idempotency_key)).fetchone()
    return row[0], False


def claim_next(db: sqlite3.Connection, job_types: list[str], worker_id: str, lease_seconds: float,
               now: float | None = None) -> Job | None:
    """Claim the next runnable job in one short transaction, or None.

    Runnable: pending, or leased with an expired lease; attempts left; due. A job whose lease
    expired after its FINAL attempt is dead-lettered here instead of being claimed again.
    """
    now = time.time() if now is None else now
    marks = ",".join("?" for _ in job_types)
    db.execute("BEGIN IMMEDIATE")
    try:
        db.execute(
            f"UPDATE jobs SET status='dead_letter', lease_token=NULL, lease_until=NULL, "
            f"last_error='lease expired after final attempt', updated_at=? "
            f"WHERE job_type IN ({marks}) AND status='leased' AND lease_until < ? AND attempts >= max_attempts",
            (now, *job_types, now))
        row = db.execute(
            f"SELECT * FROM jobs WHERE job_type IN ({marks}) AND status IN ('pending','leased') "
            f"AND attempts < max_attempts AND next_run_at <= ? AND (lease_until IS NULL OR lease_until < ?) "
            f"ORDER BY next_run_at, created_at LIMIT 1", (*job_types, now, now)).fetchone()
        if row is None:
            db.execute("COMMIT")
            return None
        token = uuid.uuid4().hex
        db.execute("UPDATE jobs SET status='leased', attempts=attempts+1, worker_id=?, lease_token=?, "
                   "lease_until=?, updated_at=? WHERE job_id=?",
                   (worker_id, token, now + lease_seconds, now, row["job_id"]))
        db.execute("COMMIT")
    except BaseException:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
    return Job(job_id=row["job_id"], job_type=row["job_type"], payload=json.loads(row["payload_json"]),
               attempts=row["attempts"] + 1, max_attempts=row["max_attempts"], lease_token=token,
               worker_id=worker_id, lease_until=now + lease_seconds,
               completed_stages=json.loads(row["completed_stages_json"]),
               remote_ops=json.loads(row["remote_ops_json"]))


def _fenced(db: sqlite3.Connection, job: Job, sets: str, args: tuple, now: float) -> None:
    """One write, allowed only while this worker still owns an unexpired lease on the job."""
    cur = db.execute(
        f"UPDATE jobs SET {sets}, updated_at=? WHERE job_id=? AND status='leased' AND lease_token=? "
        f"AND worker_id=? AND lease_until >= ?",
        (*args, now, job.job_id, job.lease_token, job.worker_id, now))
    if cur.rowcount == 0:
        raise LeaseLostError("this worker no longer owns the job")


def heartbeat(db, job: Job, lease_seconds: float, now: float | None = None) -> None:
    now = time.time() if now is None else now
    _fenced(db, job, "lease_until=?", (now + lease_seconds,), now)
    job.lease_until = now + lease_seconds


def complete(db, job: Job, now: float | None = None) -> None:
    now = time.time() if now is None else now
    _fenced(db, job, "status='completed', lease_token=NULL, lease_until=NULL", (), now)


def cancel(db, job: Job, now: float | None = None) -> None:
    now = time.time() if now is None else now
    _fenced(db, job, "status='cancelled', lease_token=NULL, lease_until=NULL, last_error=?", ("cancelled",), now)


def dead_letter(db, job: Job, error_kind: str, now: float | None = None) -> None:
    now = time.time() if now is None else now
    _fenced(db, job, "status='dead_letter', lease_token=NULL, lease_until=NULL, last_error=?", (error_kind,), now)


def retry_later(db, job: Job, delay: float, error_kind: str, now: float | None = None) -> None:
    now = time.time() if now is None else now
    _fenced(db, job, "status='pending', lease_token=NULL, lease_until=NULL, next_run_at=?, last_error=?",
            (now + delay, error_kind), now)


def record_stage(db, job: Job, stage: str, remote_op: str | None = None, now: float | None = None) -> None:
    """Checkpoint a finished stage (and any provider id) so a retry does not repeat it."""
    now = time.time() if now is None else now
    stages = list(dict.fromkeys([*job.completed_stages, stage]))
    ops = dict(job.remote_ops)
    if remote_op is not None:
        ops[stage] = remote_op
    _fenced(db, job, "completed_stages_json=?, remote_ops_json=?", (json.dumps(stages), json.dumps(ops)), now)
    job.completed_stages, job.remote_ops = stages, ops


def describe(db: sqlite3.Connection, job_id: str) -> dict[str, Any] | None:
    row = db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
    if row is None:
        return None
    return {"job_id": row["job_id"], "job_type": row["job_type"], "status": row["status"],
            "attempts": row["attempts"], "max_attempts": row["max_attempts"], "last_error": row["last_error"],
            "completed_stages": json.loads(row["completed_stages_json"]), "next_run_at": row["next_run_at"]}


# -------------------------------------------------------------------- worker

@dataclass
class Attempt:
    """What a handler is given: its job, its own connection, and the lease-aware helpers."""

    job: Job
    db: sqlite3.Connection
    _lost: threading.Event
    _now: Callable[[], float]

    def check(self) -> None:
        """Call between steps of long work. Raises if the lease was lost, so work stops early."""
        if self._lost.is_set():
            raise LeaseLostError("lease lost during the attempt")

    def done(self, stage: str) -> bool:
        return stage in self.job.completed_stages

    def record_stage(self, stage: str, remote_op: str | None = None) -> None:
        self.check()
        record_stage(self.db, self.job, stage, remote_op, self._now())


Handler = Callable[[Attempt], None]


class Worker:
    """Claims jobs and runs their handlers. One attempt: the handler runs while a heartbeat thread
    keeps the lease alive; the attempt ends in exactly one of completed / retry scheduled /
    dead-lettered / cancelled / lease lost."""

    def __init__(self, open_db: Callable[[], sqlite3.Connection], handlers: dict[str, Handler], *,
                 worker_id: str = "worker-1", lease_seconds: float = 60.0, poll_seconds: float = 1.0,
                 backoff: ExponentialBackoff | None = None, now: Callable[[], float] = time.time,
                 heartbeat: bool = True) -> None:
        if not handlers:
            raise ValueError("a worker needs at least one handler")
        if lease_seconds <= 0 or poll_seconds <= 0:
            raise ValueError("lease and poll intervals must be positive")
        self.open_db, self.handlers = open_db, handlers
        self.worker_id, self.lease_seconds, self.poll_seconds = worker_id, lease_seconds, poll_seconds
        self.backoff, self.now, self.heartbeat = backoff or ExponentialBackoff(), now, heartbeat

    def run_once(self) -> str | None:
        """Claim and run one job. Returns the outcome, or None if nothing was runnable.
        A database error while claiming is reported as "error" and never kills the worker."""
        try:
            db = self.open_db()
            job = claim_next(db, list(self.handlers), self.worker_id, self.lease_seconds, self.now())
        except sqlite3.Error:
            return "error"
        if job is None:
            return None
        return self._attempt(db, job)

    def _attempt(self, db: sqlite3.Connection, job: Job) -> str:
        lost, stop = threading.Event(), threading.Event()
        beat = threading.Thread(target=self._beat, args=(job, lost, stop), daemon=True) if self.heartbeat else None
        if beat:
            beat.start()
        try:
            self.handlers[job.job_type](Attempt(job, db, lost, self.now))
            complete(db, job, self.now())
            return "completed"
        except LeaseLostError:
            return "lease_lost"                     # not ours any more: write nothing
        except JobCancelled:
            return self._finish(lambda: cancel(db, job, self.now()), "cancelled")
        except (PermanentJobError, ValidationError) as exc:
            kind = type(exc).__name__
            return self._finish(lambda: dead_letter(db, job, kind, self.now()), "dead_letter")
        except Exception as exc:                    # noqa: BLE001 - the handler boundary
            kind = type(exc).__name__               # the type only: a message may hold private text
            if job.attempts >= job.max_attempts:
                return self._finish(lambda: dead_letter(db, job, kind, self.now()), "dead_letter")
            delay = self.backoff.next_delay(job.attempts)
            return self._finish(lambda: retry_later(db, job, delay, kind, self.now()), "retry_scheduled")
        finally:
            stop.set()
            if beat:
                beat.join(timeout=2)

    @staticmethod
    def _finish(write: Callable[[], None], outcome: str) -> str:
        try:
            write()
        except LeaseLostError:
            return "lease_lost"
        return outcome

    def _beat(self, job: Job, lost: threading.Event, stop: threading.Event) -> None:
        db = self.open_db()
        while not stop.wait(self.lease_seconds / 3):
            try:
                heartbeat(db, job, self.lease_seconds, self.now())
            except (LeaseLostError, sqlite3.Error):
                lost.set()
                return

    def run_forever(self, stop: threading.Event) -> None:
        while not stop.is_set():
            outcome = self.run_once()
            if outcome is None or outcome == "error":
                stop.wait(self.poll_seconds)
