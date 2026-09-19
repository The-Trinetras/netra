"""Uploaded notes as versioned sources, ingested by a staged, checkpointed background job.

Ported from the earlier project's source, ingestion and retrieval-authorization design, scaled
to text and Markdown on one SQLite file. What is kept, because it is what makes uploads safe:

  * Sources belong to an account. Another account's source is "not found", and retrieval only
    ever ranks passages the asking account is allowed to see. An id alone is not authority.
  * A source is a series of immutable versions. Activating a new version supersedes the old one;
    a session is PINNED to the versions it started with, so the notes cannot change under a
    conversation in progress (unless a source is deleted: deletion always wins).
  * Ingestion is a job with stages (validate, parse, chunk, embed, activate). A stage that
    finished is not repeated on retry, every stage is safe to run twice, and a failure leaves a
    reason the student can read.
  * Deleting a source removes its text: the stored copies at once, and its passages from the
    search index in a background job. Nothing that was deleted is retrievable in between.

Limits, stated plainly:
  * Text and Markdown only. The earlier project also parsed PDFs, through PyMuPDF, which is
    AGPL-licensed: a licensing decision that belongs to the project owner and is not made here.
  * Answers already given keep their quoted passages in the kit's append-only run history, which
    is append-only by design: deleting a source does not rewrite past answers.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Iterable

from . import describe
from . import jobs as J
from .corpus import NOTES_DIR, note_files

MAX_UPLOAD_CHARS = 100_000
MAX_CHUNKS = 300
MAX_SOURCES_PER_ACCOUNT = 10
MAX_NAME_CHARS = 80
ALLOWED_EXTENSIONS = (".md", ".txt")

INGEST_JOB, PURGE_JOB = "ingest_source", "purge_source"
STAGES = ("validate", "parse", "chunk", "embed", "activate")

SOURCES_MIGRATION = ("0004_sources", """
    CREATE TABLE sources (
        source_id         TEXT PRIMARY KEY,
        account_id        TEXT REFERENCES accounts(account_id),      -- NULL for the built-in samples
        name              TEXT NOT NULL,
        builtin           INTEGER NOT NULL DEFAULT 0,
        active_version_id TEXT,
        deleted_at        REAL,
        created_at        REAL NOT NULL
    );
    CREATE TABLE source_versions (
        version_id  TEXT PRIMARY KEY,
        source_id   TEXT NOT NULL REFERENCES sources(source_id),
        version_no  INTEGER NOT NULL,
        filename    TEXT NOT NULL,
        status      TEXT NOT NULL,   -- uploaded validated parsed chunked embedded active superseded failed deleted
        raw_text    TEXT,
        parsed_text TEXT,
        chunk_count INTEGER,
        error       TEXT,
        upload_key  TEXT UNIQUE,
        created_at  REAL NOT NULL,
        updated_at  REAL NOT NULL,
        UNIQUE (source_id, version_no)
    );
    CREATE TABLE source_chunks (
        version_id TEXT NOT NULL REFERENCES source_versions(version_id),
        chunk_id   TEXT NOT NULL,
        PRIMARY KEY (version_id, chunk_id)
    );
    CREATE INDEX source_chunks_by_chunk ON source_chunks(chunk_id);
    CREATE INDEX sources_by_account ON sources(account_id)
""")


class SourceError(Exception):
    """A refusal the student can be told about. `code` is stable; `message` is safe to show."""

    def __init__(self, code: str, message: str) -> None:
        self.code, self.message = code, message
        super().__init__(message)


# ------------------------------------------------------------------ helpers

def safe_filename(name: str) -> str:
    """A filename that cannot carry a path, a control character or markup into the index."""
    base = re.split(r"[\\/]", name.strip())[-1]
    base = re.sub(r"[^A-Za-z0-9._ -]", "_", base).strip(" .")[:60] or "notes"
    return base


def chunk_ids_for(filename: str, text: str) -> list[str]:
    """The kit's chunk ids for this text: it hashes the file name, position and content, so the
    same upload is idempotent and different content never collides."""
    from slice.retrieve import split
    return [hashlib.sha1(f"{filename}:{i}:{chunk}".encode()).hexdigest()[:16]
            for i, chunk in enumerate(split(text))]


def _ensure_index(store) -> None:
    from slice.retrieve import _prepare
    _prepare(store)


# ---------------------------------------------------------------- uploading

def create_source(db: sqlite3.Connection, account_id: str, name: str, filename: str, text: str, *,
                  source_id: str | None = None, upload_key: str | None = None,
                  now: float | None = None) -> dict[str, Any]:
    """Store an upload as a new version and queue its ingestion. Returns ids and the status.

    Passing `upload_key` makes it idempotent: a double-submitted form returns the first upload."""
    now = time.time() if now is None else now
    if upload_key:
        row = db.execute("SELECT v.version_id, v.source_id, v.version_no, v.status FROM source_versions v "
                         "JOIN sources s ON s.source_id = v.source_id WHERE v.upload_key=? AND s.account_id=?",
                         (upload_key, account_id)).fetchone()
        if row:
            return {"source_id": row["source_id"], "version_id": row["version_id"],
                    "version_no": row["version_no"], "status": row["status"], "duplicate": True}

    name = re.sub(r"\s+", " ", name or "").strip()
    if not name or len(name) > MAX_NAME_CHARS or any(ord(c) < 32 for c in name):
        raise SourceError("bad_name", f"Give the notes a name of up to {MAX_NAME_CHARS} characters.")
    filename = safe_filename(filename or name)
    if not filename.lower().endswith(ALLOWED_EXTENSIONS):
        filename += ".md"
    if not text or not text.strip():
        raise SourceError("empty", "There is nothing to add: the notes are empty.")
    if len(text) > MAX_UPLOAD_CHARS:
        raise SourceError("too_large", f"Keep notes under {MAX_UPLOAD_CHARS:,} characters.")

    db.execute("BEGIN IMMEDIATE")
    try:
        if source_id is None:
            count = db.execute("SELECT COUNT(*) FROM sources WHERE account_id=? AND deleted_at IS NULL",
                               (account_id,)).fetchone()[0]
            if count >= MAX_SOURCES_PER_ACCOUNT:
                raise SourceError("too_many_sources",
                                  f"You can keep up to {MAX_SOURCES_PER_ACCOUNT} sources. Delete one first.")
            source_id = f"src_{uuid.uuid4().hex[:12]}"
            db.execute("INSERT INTO sources(source_id, account_id, name, builtin, created_at) VALUES (?,?,?,0,?)",
                       (source_id, account_id, name, now))
            version_no = 1
        else:
            owned = db.execute("SELECT 1 FROM sources WHERE source_id=? AND account_id=? AND builtin=0 "
                               "AND deleted_at IS NULL", (source_id, account_id)).fetchone()
            if owned is None:
                from .sessions import NotFound
                raise NotFound(source_id)
            version_no = db.execute("SELECT COALESCE(MAX(version_no),0)+1 FROM source_versions WHERE source_id=?",
                                    (source_id,)).fetchone()[0]
        version_id = f"ver_{uuid.uuid4().hex[:12]}"
        db.execute("INSERT INTO source_versions(version_id, source_id, version_no, filename, status, raw_text, "
                   "upload_key, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                   (version_id, source_id, version_no, filename, "uploaded", text, upload_key, now, now))
        db.execute("COMMIT")
    except BaseException:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
    J.enqueue(db, INGEST_JOB, version_id, {"version_id": version_id}, now=now)
    return {"source_id": source_id, "version_id": version_id, "version_no": version_no,
            "status": "uploaded", "duplicate": False}


# ---------------------------------------------------------------- ingestion

def _set(db, version_id: str, now: float, **fields: Any) -> None:
    columns = ", ".join(f"{k}=?" for k in fields)
    db.execute(f"UPDATE source_versions SET {columns}, updated_at=? WHERE version_id=?",
               (*fields.values(), now, version_id))


def _fail(db, version_id: str, message: str, now: float) -> None:
    _set(db, version_id, now, status="failed", error=message, raw_text=None, parsed_text=None)


def embed_with_kit(store, filename: str, text: str) -> None:
    """Embed through the kit's own ingestion, unchanged: it reads a folder, so the text goes into a
    temporary file named for the upload. Chunk ids are content hashes, so running it twice adds nothing."""
    from slice.retrieve import ingest
    with tempfile.TemporaryDirectory() as folder:
        (Path(folder) / filename).write_text(text, encoding="utf-8")
        ingest(store, folder)


def make_ingest_handler(open_store: Callable[[], Any], embed: Callable[[Any, str, str], None] = embed_with_kit,
                        now: Callable[[], float] = time.time) -> J.Handler:
    """The ingestion job. Each stage is skipped if a previous attempt already finished it."""

    def handler(attempt: J.Attempt) -> None:
        db, version_id = attempt.db, attempt.job.payload["version_id"]
        try:
            _stages(attempt, db, version_id, open_store, embed, now)
        except J.JobCancelled:
            raise                                   # the source was deleted: nothing to mark
        except J.LeaseLostError:
            raise                                   # another worker owns it now: write nothing
        except J.PermanentJobError as exc:
            _fail(db, version_id, str(exc) or "This file could not be used.", now())
            raise
        except Exception:                           # noqa: BLE001
            if attempt.job.attempts >= attempt.job.max_attempts:
                _fail(db, version_id, "Processing failed. Please try again.", now())
            raise

    return handler


def _stages(attempt, db, version_id, open_store, embed, now) -> None:
    row = db.execute("SELECT v.*, s.deleted_at FROM source_versions v JOIN sources s ON s.source_id=v.source_id "
                     "WHERE v.version_id=?", (version_id,)).fetchone()
    if row is None or row["deleted_at"] is not None or row["status"] == "deleted":
        raise J.JobCancelled()
    filename = row["filename"]

    if not attempt.done("validate"):
        text = row["raw_text"] or ""
        if not filename.lower().endswith(ALLOWED_EXTENSIONS):
            raise J.PermanentJobError("Only .md and .txt notes are supported.")
        if not text.strip():
            raise J.PermanentJobError("The notes are empty.")
        if len(text) > MAX_UPLOAD_CHARS:
            raise J.PermanentJobError(f"Notes must be under {MAX_UPLOAD_CHARS:,} characters.")
        controls = sum(1 for c in text if ord(c) < 32 and c not in "\n\r\t")
        if "\x00" in text or controls > max(3, len(text) // 50):
            raise J.PermanentJobError("This does not look like a text file.")
        _set(db, version_id, now(), status="validated")
        attempt.record_stage("validate")
    attempt.check()

    if not attempt.done("parse"):
        text = (row["raw_text"] or "").lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"
        _set(db, version_id, now(), status="parsed", parsed_text=describe.annotate(text), raw_text=None)
        attempt.record_stage("parse")
    attempt.check()

    parsed = db.execute("SELECT parsed_text FROM source_versions WHERE version_id=?", (version_id,)).fetchone()[0]

    if not attempt.done("chunk"):
        ids = chunk_ids_for(filename, parsed)
        if not ids:
            raise J.PermanentJobError("Nothing could be read from these notes.")
        if len(ids) > MAX_CHUNKS:
            raise J.PermanentJobError("These notes are too long to index. Split them into parts.")
        _set(db, version_id, now(), status="chunked", chunk_count=len(ids))
        attempt.record_stage("chunk")
    attempt.check()

    if not attempt.done("embed"):
        store = open_store()
        embed(store, filename, parsed)
        _ensure_index(store)
        ids = chunk_ids_for(filename, parsed)
        present = {r[0] for r in store.db.execute(
            f"SELECT chunk_id FROM chunks WHERE chunk_id IN ({','.join('?' * len(ids))})", ids)}
        if present != set(ids):                     # transient: the retry embeds again
            raise RuntimeError("embedding did not store every passage")
        db.executemany("INSERT OR IGNORE INTO source_chunks(version_id, chunk_id) VALUES (?,?)",
                       [(version_id, cid) for cid in ids])
        _set(db, version_id, now(), status="embedded")
        attempt.record_stage("embed")
    attempt.check()

    if not attempt.done("activate"):
        _activate(db, version_id, now())
        attempt.record_stage("activate")


def _activate(db: sqlite3.Connection, version_id: str, now: float) -> None:
    """Make this the active version, in one short transaction. A source deleted meanwhile is not revived."""
    db.execute("BEGIN IMMEDIATE")
    try:
        row = db.execute("SELECT v.source_id, s.deleted_at, s.active_version_id FROM source_versions v "
                         "JOIN sources s ON s.source_id=v.source_id WHERE v.version_id=?", (version_id,)).fetchone()
        if row is None or row["deleted_at"] is not None:
            raise J.JobCancelled()
        if row["active_version_id"] and row["active_version_id"] != version_id:
            db.execute("UPDATE source_versions SET status='superseded', updated_at=? "
                       "WHERE version_id=? AND status='active'", (now, row["active_version_id"]))
        db.execute("UPDATE source_versions SET status='active', updated_at=? WHERE version_id=?", (now, version_id))
        db.execute("UPDATE sources SET active_version_id=? WHERE source_id=?", (version_id, row["source_id"]))
        db.execute("COMMIT")
    except BaseException:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise


# ----------------------------------------------------------------- deletion

def delete_source(db: sqlite3.Connection, account_id: str, source_id: str, now: float | None = None) -> None:
    """Delete a source the account owns. It stops being retrievable immediately; its stored text goes at
    once; its passages leave the search index in a background job."""
    now = time.time() if now is None else now
    db.execute("BEGIN IMMEDIATE")
    try:
        row = db.execute("SELECT 1 FROM sources WHERE source_id=? AND account_id=? AND builtin=0",
                         (source_id, account_id)).fetchone()
        if row is None:
            from .sessions import NotFound
            raise NotFound(source_id)
        db.execute("UPDATE sources SET deleted_at=COALESCE(deleted_at, ?), active_version_id=NULL WHERE source_id=?",
                   (now, source_id))
        db.execute("UPDATE source_versions SET status='deleted', raw_text=NULL, parsed_text=NULL, updated_at=? "
                   "WHERE source_id=?", (now, source_id))
        db.execute("COMMIT")
    except BaseException:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
    J.enqueue(db, PURGE_JOB, source_id, {"source_id": source_id}, now=now)


def make_purge_handler(open_store: Callable[[], Any]) -> J.Handler:
    """Remove a deleted source's passages from the search index, unless another live source still uses
    the same passage. Safe to run twice."""

    def handler(attempt: J.Attempt) -> None:
        db, source_id = attempt.db, attempt.job.payload["source_id"]
        versions = [r[0] for r in db.execute("SELECT version_id FROM source_versions WHERE source_id=?", (source_id,))]
        if not versions:
            return
        marks = ",".join("?" * len(versions))
        mine = [r[0] for r in db.execute(f"SELECT DISTINCT chunk_id FROM source_chunks WHERE version_id IN ({marks})",
                                         versions)]
        db.execute(f"DELETE FROM source_chunks WHERE version_id IN ({marks})", versions)
        attempt.check()
        store = open_store()
        _ensure_index(store)
        for chunk_id in mine:
            if db.execute("SELECT 1 FROM source_chunks WHERE chunk_id=? LIMIT 1", (chunk_id,)).fetchone():
                continue                            # still used by another source: keep it
            store.db.execute("DELETE FROM chunk_vec WHERE chunk_id=?", (chunk_id,))
            store.db.execute("DELETE FROM chunks WHERE chunk_id=?", (chunk_id,))

    return handler


# ------------------------------------------------------------------ reading

def list_sources(db: sqlite3.Connection, account_id: str) -> list[dict[str, Any]]:
    """The account's own sources and the built-in samples, newest first. Deleted ones are not shown."""
    rows = db.execute(
        "SELECT s.source_id, s.name, s.builtin, s.active_version_id, "
        "  (SELECT v.status FROM source_versions v WHERE v.source_id=s.source_id ORDER BY v.version_no DESC LIMIT 1) AS latest_status, "
        "  (SELECT v.error FROM source_versions v WHERE v.source_id=s.source_id ORDER BY v.version_no DESC LIMIT 1) AS latest_error, "
        "  (SELECT MAX(v.version_no) FROM source_versions v WHERE v.source_id=s.source_id) AS latest_no, "
        "  (SELECT v.chunk_count FROM source_versions v WHERE v.version_id=s.active_version_id) AS chunks "
        "FROM sources s WHERE s.deleted_at IS NULL AND (s.account_id=? OR s.builtin=1) "
        "ORDER BY s.builtin, s.created_at DESC", (account_id,)).fetchall()
    out = []
    for r in rows:
        status = r["latest_status"]
        state = ("ready" if status == "active" else "failed" if status == "failed"
                 else "ready" if r["active_version_id"] else "processing")
        out.append({"source_id": r["source_id"], "name": r["name"], "builtin": bool(r["builtin"]),
                    "state": state, "version": r["latest_no"], "passages": r["chunks"],
                    "error": r["latest_error"] if status == "failed" else None,
                    "updating": status not in ("active", "failed", "superseded", "deleted") and bool(r["active_version_id"])})
    return out


def active_version_ids(db: sqlite3.Connection, account_id: str) -> list[str]:
    """The versions a NEW session pins: the active version of every live source the account can use."""
    return [r[0] for r in db.execute(
        "SELECT s.active_version_id FROM sources s WHERE s.deleted_at IS NULL AND s.active_version_id IS NOT NULL "
        "AND (s.account_id=? OR s.builtin=1) ORDER BY s.source_id", (account_id,))]


def allowed_chunk_ids(db: sqlite3.Connection, account_id: str, version_ids: Iterable[str]) -> set[str]:
    """The passages retrieval may return. Only pinned versions, only of sources that are not deleted, only
    sources this account owns or the built-in samples. This is the authoritative check, made per request."""
    versions = list(version_ids)
    if not versions:
        return set()
    marks = ",".join("?" * len(versions))
    rows = db.execute(
        f"SELECT DISTINCT c.chunk_id FROM source_chunks c JOIN source_versions v ON v.version_id=c.version_id "
        f"JOIN sources s ON s.source_id=v.source_id WHERE c.version_id IN ({marks}) AND s.deleted_at IS NULL "
        f"AND v.status IN ('active','superseded') AND (s.account_id=? OR s.builtin=1)", (*versions, account_id))
    return {r[0] for r in rows}


def register_builtin(db: sqlite3.Connection, now: float | None = None) -> None:
    """Register the sample notes (already embedded by the kit) as built-in sources every account can use.
    Idempotent."""
    now = time.time() if now is None else now
    for path in note_files():
        name = f"Sample: {path.stem}"
        if db.execute("SELECT 1 FROM sources WHERE builtin=1 AND name=?", (name,)).fetchone():
            continue
        text = path.read_text(encoding="utf-8")
        source_id, version_id = f"src_{uuid.uuid4().hex[:12]}", f"ver_{uuid.uuid4().hex[:12]}"
        ids = chunk_ids_for(path.name, text)
        db.execute("BEGIN IMMEDIATE")
        db.execute("INSERT INTO sources(source_id, account_id, name, builtin, active_version_id, created_at) "
                   "VALUES (?,NULL,?,1,?,?)", (source_id, name, version_id, now))
        db.execute("INSERT INTO source_versions(version_id, source_id, version_no, filename, status, chunk_count, "
                   "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                   (version_id, source_id, 1, path.name, "active", len(ids), now, now))
        db.executemany("INSERT OR IGNORE INTO source_chunks(version_id, chunk_id) VALUES (?,?)",
                       [(version_id, cid) for cid in ids])
        db.execute("COMMIT")
