"""Uploaded sources: versions, the staged ingestion job, deletion, and who may see what.

The pipeline tests use a fake embedder (no model), so they run anywhere. The last group uses the
kit's real embeddings, because "another account's notes never come back" is only worth proving in
what search actually returns; it skips itself where the embedding model is not installed.
"""
from __future__ import annotations

import hashlib

import pytest

from demo.notes import describe
from demo.notes import jobs as J
from demo.notes import sessions as S
from demo.notes import sources as SRC
from slice.retrieve import split
from slice.store import Store

NOTES = """# Capacitors

A capacitor stores charge. Its capacitance is measured in farads.

| Component | Symbol |
|-----------|--------|
| Capacitor | C      |

The charge is Q = C x V.
"""


class Clock:
    def __init__(self, t: float = 5000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def fake_embed(store, filename: str, text: str) -> None:
    """Stores passages in the kit's chunk table the way the kit's ingestion would, without a model."""
    from slice.retrieve import _prepare
    _prepare(store)
    for i, chunk in enumerate(split(text)):
        cid = hashlib.sha1(f"{filename}:{i}:{chunk}".encode()).hexdigest()[:16]
        store.db.execute("INSERT OR IGNORE INTO chunks(chunk_id, doc, ordinal, text) VALUES (?,?,?,?)",
                         (cid, filename, i, chunk))


@pytest.fixture
def path(tmp_path):
    p = str(tmp_path / "s.db")
    S.migrate(Store(p).db)
    return p


@pytest.fixture
def db(path):
    return Store(path).db


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def account(db):
    return S.create_account(db, "asha")


def _worker(path, clock, embed=fake_embed, **kw):
    open_store = lambda: Store(path)          # noqa: E731
    handlers = {SRC.INGEST_JOB: SRC.make_ingest_handler(open_store, embed, clock),
                SRC.PURGE_JOB: SRC.make_purge_handler(open_store)}
    kw.setdefault("heartbeat", False)
    kw.setdefault("backoff", J.ExponentialBackoff(base=10, jitter=0.0))
    return J.Worker(lambda: Store(path).db, handlers, now=clock, **kw)


def _drain(worker):
    outcomes = []
    for _ in range(30):
        outcome = worker.run_once()
        if outcome is None:
            return outcomes
        outcomes.append(outcome)
    raise AssertionError("the worker never went idle")


def _upload(db, account, text=NOTES, name="Capacitors", clock=None, **kw):
    return SRC.create_source(db, account, name, "capacitors.md", text, now=clock() if clock else None, **kw)


def _version(db, version_id):
    return dict(db.execute("SELECT * FROM source_versions WHERE version_id=?", (version_id,)).fetchone())


# ---------------------------------------------------------------- uploading

def test_an_upload_becomes_version_one_and_queues_its_ingestion(db, account, clock):
    made = _upload(db, account, clock=clock)
    assert made["version_no"] == 1 and made["status"] == "uploaded" and made["duplicate"] is False
    version = _version(db, made["version_id"])
    assert version["raw_text"] == NOTES and version["filename"] == "capacitors.md"
    job = db.execute("SELECT job_type, idempotency_key, status FROM jobs").fetchone()
    assert tuple(job) == (SRC.INGEST_JOB, made["version_id"], "pending")


@pytest.mark.parametrize("name, text, code", [
    ("", "text", "bad_name"), ("x" * 81, "text", "bad_name"), ("bad\x00name", "text", "bad_name"),
    ("ok", "", "empty"), ("ok", "   \n\t ", "empty"), pytest.param("ok", "x" * (SRC.MAX_UPLOAD_CHARS + 1), "too_large", id="too_large"),
])
def test_bad_uploads_are_refused_with_a_reason_and_store_nothing(db, account, name, text, code):
    with pytest.raises(SRC.SourceError) as info:
        SRC.create_source(db, account, name, "n.md", text)
    assert info.value.code == code
    assert db.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


def test_an_account_can_keep_only_so_many_sources(db, account):
    for i in range(SRC.MAX_SOURCES_PER_ACCOUNT):
        SRC.create_source(db, account, f"n{i}", "n.md", "text")
    with pytest.raises(SRC.SourceError) as info:
        SRC.create_source(db, account, "one more", "n.md", "text")
    assert info.value.code == "too_many_sources"


def test_file_names_cannot_carry_a_path_or_markup_into_the_index():
    assert SRC.safe_filename("../../etc/passwd") == "passwd"
    assert SRC.safe_filename("C:\\Users\\me\\notes.md") == "notes.md"
    assert SRC.safe_filename('a"><b>.md') == "a___b_.md"
    assert SRC.safe_filename("   ") == "notes"


def test_a_name_without_a_supported_extension_gets_md(db, account):
    made = SRC.create_source(db, account, "My notes", "my notes", "text")
    assert _version(db, made["version_id"])["filename"] == "my notes.md"


def test_the_same_upload_key_returns_the_first_upload_and_queues_one_job(db, account):
    first = _upload(db, account, upload_key="form-1")
    again = _upload(db, account, upload_key="form-1")
    assert again["duplicate"] is True and again["version_id"] == first["version_id"]
    assert db.execute("SELECT COUNT(*) FROM source_versions").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1


def test_a_new_version_of_my_source_gets_the_next_number_and_nobody_elses_can_be_touched(db, account):
    first = _upload(db, account)
    second = SRC.create_source(db, account, "Capacitors", "capacitors.md", "v2 text", source_id=first["source_id"])
    assert second["version_no"] == 2 and second["source_id"] == first["source_id"]

    other = S.create_account(db, "other")
    with pytest.raises(S.NotFound):
        SRC.create_source(db, other, "x", "x.md", "text", source_id=first["source_id"])


# ---------------------------------------------------------------- ingestion

def test_the_pipeline_runs_every_stage_and_activates_the_version(path, db, account, clock):
    made = _upload(db, account, clock=clock)
    assert _drain(_worker(path, clock)) == ["completed"]

    version = _version(db, made["version_id"])
    assert version["status"] == "active" and version["raw_text"] is None, "the raw upload should not be kept"
    assert "[Table read aloud: Table with 2 columns" in version["parsed_text"]
    assert "[Read aloud: Q equals C times V.]" in version["parsed_text"]
    mapped = db.execute("SELECT COUNT(*) FROM source_chunks WHERE version_id=?", (made["version_id"],)).fetchone()[0]
    assert mapped == version["chunk_count"] > 0
    job = J.describe(db, db.execute("SELECT job_id FROM jobs").fetchone()[0])
    assert job["status"] == "completed" and job["completed_stages"] == list(SRC.STAGES)
    assert db.execute("SELECT active_version_id FROM sources").fetchone()[0] == made["version_id"]


def test_a_new_version_supersedes_the_old_one(path, db, account, clock):
    first = _upload(db, account, clock=clock)
    _drain(_worker(path, clock))
    second = SRC.create_source(db, account, "Capacitors", "capacitors.md", "A different set of notes.",
                               source_id=first["source_id"], now=clock())
    _drain(_worker(path, clock))
    assert _version(db, first["version_id"])["status"] == "superseded"
    assert _version(db, second["version_id"])["status"] == "active"
    assert db.execute("SELECT active_version_id FROM sources").fetchone()[0] == second["version_id"]


@pytest.mark.parametrize("filename, text, message", [
    ("notes.pdf", "text", "Only .md and .txt"),
    ("notes.md", "text with a null \x00 byte", "does not look like a text file"),
    ("notes.md", "\x01\x02\x03\x04\x05\x06 binary", "does not look like a text file"),
])
def test_a_file_that_can_never_be_read_fails_with_a_reason_and_is_not_retried(path, db, account, clock,
                                                                               filename, text, message):
    made = _upload(db, account, text=text, clock=clock)
    db.execute("UPDATE source_versions SET filename=? WHERE version_id=?", (filename, made["version_id"]))
    assert _drain(_worker(path, clock)) == ["dead_letter"]
    version = _version(db, made["version_id"])
    assert version["status"] == "failed" and message in version["error"]
    assert version["raw_text"] is None and version["parsed_text"] is None, "a failed upload should not be kept"
    assert db.execute("SELECT attempts FROM jobs").fetchone()[0] == 1


def test_notes_with_too_many_passages_are_refused_with_advice(path, db, account, clock, monkeypatch):
    monkeypatch.setattr(SRC, "MAX_CHUNKS", 1)
    made = _upload(db, account, text="para one\n\n" + "x" * 900 + "\n\n" + "y" * 900, clock=clock)
    _drain(_worker(path, clock))
    assert "too long to index" in _version(db, made["version_id"])["error"]


def test_a_transient_failure_is_retried_and_finished_stages_are_not_repeated(path, db, account, clock, monkeypatch):
    parses = []
    real_annotate = describe.annotate
    monkeypatch.setattr(describe, "annotate", lambda t: parses.append(1) or real_annotate(t))
    embeds = {"n": 0}

    def flaky(store, filename, text):
        embeds["n"] += 1
        if embeds["n"] == 1:
            raise RuntimeError("the embedding model was busy")
        fake_embed(store, filename, text)

    made = _upload(db, account, clock=clock)
    worker = _worker(path, clock, embed=flaky)
    assert worker.run_once() == "retry_scheduled"
    job_id = db.execute("SELECT job_id FROM jobs").fetchone()[0]
    assert J.describe(db, job_id)["completed_stages"] == ["validate", "parse", "chunk"]

    clock.advance(30)
    assert worker.run_once() == "completed"
    assert embeds["n"] == 2 and len(parses) == 1, "a finished stage ran again"
    assert _version(db, made["version_id"])["status"] == "active"


def test_a_failure_on_the_final_attempt_leaves_a_readable_reason(path, db, account, clock):
    made = _upload(db, account, clock=clock)
    db.execute("UPDATE jobs SET max_attempts=1")

    def broken(store, filename, text):
        raise RuntimeError("PRIVATE model detail")

    assert _drain(_worker(path, clock, embed=broken)) == ["dead_letter"]
    version = _version(db, made["version_id"])
    assert version["status"] == "failed" and version["error"] == "Processing failed. Please try again."
    assert "PRIVATE" not in str(version)


def test_an_embedding_that_stores_nothing_is_a_failure_not_a_silent_success(path, db, account, clock):
    made = _upload(db, account, clock=clock)
    worker = _worker(path, clock, embed=lambda store, filename, text: None)
    assert worker.run_once() == "retry_scheduled"
    assert _version(db, made["version_id"])["status"] == "chunked", "activated without passages in the index"


def test_a_source_deleted_before_ingestion_is_never_revived(path, db, account, clock):
    made = _upload(db, account, clock=clock)
    SRC.delete_source(db, account, made["source_id"], now=clock())
    outcomes = _drain(_worker(path, clock))
    assert "cancelled" in outcomes
    assert _version(db, made["version_id"])["status"] == "deleted"
    assert db.execute("SELECT active_version_id FROM sources").fetchone()[0] is None


def test_a_source_deleted_while_it_was_being_embedded_is_not_activated(path, db, account, clock):
    made = _upload(db, account, clock=clock)

    def deleting_embed(store, filename, text):
        SRC.delete_source(Store(path).db, account, made["source_id"], now=clock())
        fake_embed(store, filename, text)

    outcomes = _drain(_worker(path, clock, embed=deleting_embed))
    assert "cancelled" in outcomes and SRC.list_sources(db, account) == []
    assert db.execute("SELECT active_version_id FROM sources").fetchone()[0] is None


# ----------------------------------------------------------------- deletion

def test_deleting_removes_the_text_and_the_source_at_once_and_the_passages_after_the_purge(path, db, account, clock):
    made = _upload(db, account, clock=clock)
    _drain(_worker(path, clock))
    pinned = SRC.active_version_ids(db, account)
    assert SRC.allowed_chunk_ids(db, account, pinned), "setup: the notes should be retrievable"
    assert store_chunks(path) > 0

    SRC.delete_source(db, account, made["source_id"], now=clock())
    assert SRC.allowed_chunk_ids(db, account, pinned) == set(), "deleted notes were still retrievable"
    assert SRC.list_sources(db, account) == []
    version = _version(db, made["version_id"])
    assert version["status"] == "deleted" and version["parsed_text"] is None

    assert _drain(_worker(path, clock)) == ["completed"]
    assert store_chunks(path) == 0, "the deleted passages are still in the search index"
    assert db.execute("SELECT COUNT(*) FROM source_chunks").fetchone()[0] == 0


def store_chunks(path) -> int:
    return Store(path).db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]


def test_a_passage_two_accounts_share_survives_one_of_them_deleting(path, db, account, clock):
    other = S.create_account(db, "other")
    mine = _upload(db, account, clock=clock)
    theirs = _upload(db, other, clock=clock)            # identical text and file name: identical passages
    _drain(_worker(path, clock))
    before = store_chunks(path)

    SRC.delete_source(db, account, mine["source_id"], now=clock())
    _drain(_worker(path, clock))
    assert store_chunks(path) == before, "purging one account's copy removed the other account's passages"
    assert SRC.allowed_chunk_ids(db, other, SRC.active_version_ids(db, other))


def test_only_the_owner_can_delete_and_deleting_twice_is_harmless(db, account):
    made = _upload(db, account)
    other = S.create_account(db, "other")
    with pytest.raises(S.NotFound):
        SRC.delete_source(db, other, made["source_id"])
    SRC.delete_source(db, account, made["source_id"])
    SRC.delete_source(db, account, made["source_id"])
    assert db.execute("SELECT COUNT(*) FROM jobs WHERE job_type=?", (SRC.PURGE_JOB,)).fetchone()[0] == 1


# ------------------------------------------------------------------- access

def test_retrieval_is_allowed_only_for_pinned_live_sources_of_this_account_and_the_samples(path, db, account, clock):
    other = S.create_account(db, "other")
    mine, theirs = _upload(db, account, clock=clock), _upload(db, other, text="other notes", name="Theirs", clock=clock)
    _drain(_worker(path, clock))
    SRC.register_builtin(db)

    mine_pin = SRC.active_version_ids(db, account)
    assert mine["version_id"] in mine_pin and theirs["version_id"] not in mine_pin
    allowed = SRC.allowed_chunk_ids(db, account, mine_pin)
    own = {r[0] for r in db.execute("SELECT chunk_id FROM source_chunks WHERE version_id=?", (mine["version_id"],))}
    theirs_ids = {r[0] for r in db.execute("SELECT chunk_id FROM source_chunks WHERE version_id=?", (theirs["version_id"],))}
    assert own <= allowed and not (theirs_ids & allowed - own)

    # Asking with someone else's version id pinned does not make it readable.
    assert SRC.allowed_chunk_ids(db, account, [theirs["version_id"]]) == set()
    assert SRC.allowed_chunk_ids(db, account, []) == set()


def test_a_session_stays_on_the_version_it_started_with_until_that_source_is_deleted(path, db, account, clock):
    first = _upload(db, account, clock=clock)
    _drain(_worker(path, clock))
    pinned = SRC.active_version_ids(db, account)
    second = SRC.create_source(db, account, "Capacitors", "capacitors.md", "brand new notes about resistors",
                               source_id=first["source_id"], now=clock())
    _drain(_worker(path, clock))

    old = {r[0] for r in db.execute("SELECT chunk_id FROM source_chunks WHERE version_id=?", (first["version_id"],))}
    new = {r[0] for r in db.execute("SELECT chunk_id FROM source_chunks WHERE version_id=?", (second["version_id"],))}
    assert SRC.allowed_chunk_ids(db, account, pinned) == old, "the pinned session moved to the new version"
    assert SRC.allowed_chunk_ids(db, account, SRC.active_version_ids(db, account)) == new

    SRC.delete_source(db, account, first["source_id"])
    assert SRC.allowed_chunk_ids(db, account, pinned) == set(), "deletion must win over pinning"


def test_the_source_list_shows_only_my_sources_and_the_samples_with_their_state(path, db, account, clock):
    other = S.create_account(db, "other")
    _upload(db, other, name="Not mine", clock=clock)
    ok = _upload(db, account, name="Mine", clock=clock)
    bad = SRC.create_source(db, account, "Broken", "broken.md", "null \x00 byte", now=clock())
    SRC.register_builtin(db)
    assert {s["name"]: s["state"] for s in SRC.list_sources(db, account)}["Mine"] == "processing"
    _drain(_worker(path, clock))

    listed = {s["name"]: s for s in SRC.list_sources(db, account)}
    assert "Not mine" not in listed
    assert listed["Mine"]["state"] == "ready" and listed["Mine"]["passages"] > 0
    assert listed["Broken"]["state"] == "failed" and "text file" in listed["Broken"]["error"]
    assert any(s["builtin"] for s in listed.values())
    assert ok["source_id"] in {s["source_id"] for s in SRC.list_sources(db, account)} and bad


def test_the_built_in_samples_are_registered_once_visible_to_everyone_and_not_deletable(db, account):
    SRC.register_builtin(db)
    SRC.register_builtin(db)
    assert db.execute("SELECT COUNT(*) FROM sources WHERE builtin=1").fetchone()[0] == 3
    other = S.create_account(db, "other")
    assert {s["name"] for s in SRC.list_sources(db, account)} == {s["name"] for s in SRC.list_sources(db, other)}
    sample = SRC.list_sources(db, account)[0]["source_id"]
    with pytest.raises(S.NotFound):
        SRC.delete_source(db, account, sample)
    with pytest.raises(S.NotFound):
        SRC.create_source(db, account, "x", "x.md", "text", source_id=sample)


# ------------------------------------------------ real embeddings, real search

def _real(path, clock):
    pytest.importorskip("sqlite_vec")
    pytest.importorskip("fastembed")
    return _worker(path, clock, embed=SRC.embed_with_kit)


def _search(path, db, account, query, k=5):
    from demo.notes.corpus import search_notes
    allowed = SRC.allowed_chunk_ids(db, account, SRC.active_version_ids(db, account))
    return search_notes(Store(path), query, k=k, allowed=allowed)


def test_search_returns_my_notes_and_never_another_accounts_even_on_an_exact_keyword_match(path, db, account, clock):
    worker = _real(path, clock)
    other = S.create_account(db, "other")
    _upload(db, account, text="Zebrafish have stripes along their bodies.", name="Fish", clock=clock)
    _upload(db, other, text="The secret passphrase is quokka-velvet-42.", name="Private", clock=clock)
    _drain(worker)

    mine = _search(path, db, account, "What is the secret passphrase quokka-velvet-42?")
    assert all("quokka" not in c.text for c in mine), "another account's private notes were retrieved"
    assert any("Zebrafish" in c.text for c in _search(path, db, account, "stripes on zebrafish"))
    theirs = _search(path, db, other, "secret passphrase")
    assert any("quokka-velvet-42" in c.text for c in theirs)


def test_after_deleting_and_purging_nothing_of_the_source_can_be_found(path, db, account, clock):
    worker = _real(path, clock)
    made = _upload(db, account, text="Axolotls can regrow their limbs.", name="Axolotl", clock=clock)
    _drain(worker)
    assert any("Axolotls" in c.text for c in _search(path, db, account, "regrow limbs axolotl"))
    SRC.delete_source(db, account, made["source_id"], now=clock())
    assert _search(path, db, account, "regrow limbs axolotl") == [], "deleted notes were retrievable"
    _drain(worker)
    assert Store(path).db.execute("SELECT COUNT(*) FROM chunks WHERE text LIKE '%Axolotl%'").fetchone()[0] == 0


def test_an_empty_allowed_set_returns_nothing_rather_than_everything(path, db, account, clock):
    worker = _real(path, clock)
    _upload(db, account, text="Some notes about lamps.", clock=clock)
    _drain(worker)
    from demo.notes.corpus import search_notes
    assert search_notes(Store(path), "lamps", k=3, allowed=set()) == []


# ------------------------------------------------------------------ service

def test_uploads_and_deletes_are_refused_outside_live_mode(tmp_path):
    from demo.notes.service import NotesService, ScriptedProvider, ServiceError
    from slice.config import settings as load_settings
    service = NotesService(str(tmp_path / "s.db"), ScriptedProvider(load_settings()))
    account = S.create_account(service._open().db, "asha")
    with pytest.raises(ServiceError) as info:
        service.upload_source(account, "n", "n.md", "text")
    assert info.value.code == "uploads_unavailable"
    with pytest.raises(ServiceError):
        service.delete_source(account, "src_x")
    assert service.list_sources(account) == []
