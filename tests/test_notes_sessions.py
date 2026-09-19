"""Accounts, credentials, sessions, idempotent requests and migrations.

No model and no network: the "work" a request runs is a plain function here. What matters is
the guarantees around it - ownership, versions, replay, one-at-a-time, cancel, no transaction
held across the work, and migrations that cannot be silently rewritten.
"""
from __future__ import annotations

import sqlite3

import pytest

from demo.notes import sessions as S
from slice.store import Store


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "s.db")


@pytest.fixture
def db(db_path):
    connection = Store(db_path).db
    S.migrate(connection)
    return connection


def _account(db, name="asha"):
    account = S.create_account(db, name)
    return account, S.issue_token(db, account)


def _ok(new_state=None, result=None):
    return lambda session: (new_state if new_state is not None else {"n": session.state.get("n", 0) + 1},
                            result if result is not None else {"status": "ok"})


# --------------------------------------------------------------- migrations

def test_migrations_apply_once_and_are_recorded(tmp_path):
    connection = Store(str(tmp_path / "m.db")).db
    first = S.migrate(connection)
    assert first == [name for name, _ in S.MIGRATIONS] and first[:2] == [
        "0001_accounts_credentials_sessions", "0002_session_requests"]
    assert S.migrate(connection) == []
    rows = connection.execute("SELECT name FROM schema_migrations ORDER BY name").fetchall()
    assert [r[0] for r in rows] == first


def test_an_applied_migration_that_was_edited_is_refused_and_nothing_more_is_applied(tmp_path):
    connection = Store(str(tmp_path / "m.db")).db
    S.migrate(connection, S.MIGRATIONS[:1])
    edited = [(S.MIGRATIONS[0][0], S.MIGRATIONS[0][1] + "\n-- quietly changed"), S.MIGRATIONS[1]]
    with pytest.raises(S.MigrationTampered, match="add a new one"):
        S.migrate(connection, edited)
    tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "session_requests" not in tables, "a later migration ran despite the tampering"


def test_a_failing_migration_rolls_back_completely(tmp_path):
    connection = Store(str(tmp_path / "m.db")).db
    broken = [("0001_bad", "CREATE TABLE ok_table (x TEXT); CREATE TABLE ok_table (y TEXT)")]
    with pytest.raises(sqlite3.OperationalError):
        S.migrate(connection, broken)
    tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "ok_table" not in tables
    assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 0


# ----------------------------------------------------------------- identity

def test_the_plaintext_token_is_never_stored_only_its_hash(db):
    account = S.create_account(db, "asha")
    token = S.issue_token(db, account)
    stored = " ".join(str(v) for row in db.execute("SELECT * FROM credentials") for v in row)
    assert token not in stored and token.startswith("nt_")
    assert S.authenticate(db, token) == account


def test_unknown_empty_and_revoked_tokens_authenticate_as_nobody(db):
    account, token = _account(db)
    assert S.authenticate(db, "nt_not-a-real-token") is None
    assert S.authenticate(db, "") is None and S.authenticate(db, None) is None
    S.revoke_token(db, token)
    assert S.authenticate(db, token) is None


# ---------------------------------------------------------- authorization

def test_another_accounts_session_is_not_found_at_every_entry_point(db):
    mine, _ = _account(db, "mine")
    theirs, _ = _account(db, "theirs")
    session = S.create_session(db, theirs)

    with pytest.raises(S.NotFound):
        S.get_session(db, mine, session.session_id)
    with pytest.raises(S.NotFound):
        S.handle_request(db, mine, session.session_id, "r1", "fp", 1, _ok())
    with pytest.raises(S.NotFound):
        S.request_cancel(db, mine, session.session_id)
    with pytest.raises(S.NotFound):
        S.get_session(db, mine, "sess_does_not_exist")   # same answer as another account's id


# ------------------------------------------------------- versions + replay

def test_a_request_runs_once_advances_the_version_and_records_its_result(db):
    account, _ = _account(db)
    session = S.create_session(db, account)
    result, after, replayed = S.handle_request(db, account, session.session_id, "r1", "fp", 1,
                                               _ok({"n": 1}, {"status": "ok", "n": 1}))
    assert (result["n"], after.version, replayed) == (1, 2, False)
    assert after.state == {"n": 1}


def test_a_retry_replays_the_recorded_result_and_does_not_run_or_advance_again(db):
    account, _ = _account(db)
    session = S.create_session(db, account)
    calls = []

    def work(s):
        calls.append(1)
        return {"n": 1}, {"status": "ok"}

    S.handle_request(db, account, session.session_id, "r1", "fp", 1, work)
    result, after, replayed = S.handle_request(db, account, session.session_id, "r1", "fp", 1, work)
    assert replayed is True and result == {"status": "ok"} and after.version == 2
    assert len(calls) == 1, "the work ran twice"


def test_a_replay_is_answered_even_if_the_session_has_moved_on(db):
    """Replay comes BEFORE the version check: a client that lost the first reply and retries
    must get its answer, not a conflict about a version that its own request advanced."""
    account, _ = _account(db)
    session = S.create_session(db, account)
    S.handle_request(db, account, session.session_id, "r1", "fp1", 1, _ok())
    S.handle_request(db, account, session.session_id, "r2", "fp2", 2, _ok())   # moves to version 3
    result, after, replayed = S.handle_request(db, account, session.session_id, "r1", "fp1", 1, _ok())
    assert replayed is True and after.version == 3


def test_reusing_a_request_id_for_different_content_is_an_error_not_a_replay(db):
    account, _ = _account(db)
    session = S.create_session(db, account)
    S.handle_request(db, account, session.session_id, "r1", "fp-A", 1, _ok())
    with pytest.raises(S.RequestIdReused):
        S.handle_request(db, account, session.session_id, "r1", "fp-B", 1, _ok())


def test_a_stale_version_is_refused_and_its_work_never_runs(db):
    account, _ = _account(db)
    session = S.create_session(db, account)
    S.handle_request(db, account, session.session_id, "r1", "fp1", 1, _ok())
    ran = []
    with pytest.raises(S.VersionConflict) as info:
        S.handle_request(db, account, session.session_id, "r2", "fp2", 1,
                         lambda s: ran.append(1) or ({}, {}))
    assert (info.value.expected, info.value.actual) == (1, 2) and ran == []


def test_the_version_only_ever_goes_up(db):
    account, _ = _account(db)
    session = S.create_session(db, account)
    versions = [session.version]
    for i in range(4):
        _, after, _ = S.handle_request(db, account, session.session_id, f"r{i}", f"fp{i}", versions[-1], _ok())
        versions.append(after.version)
    assert versions == [1, 2, 3, 4, 5]


# ---------------------------------------------- one at a time, no held lock

def test_a_second_request_while_one_is_running_is_refused_as_busy(db):
    account, _ = _account(db)
    session = S.create_session(db, account)
    seen = {}

    def work(s):
        for label, rid in (("other", "r2"), ("same", "r1")):
            try:
                S.handle_request(db, account, s.session_id, rid, "fp2" if rid == "r2" else "fp1", 1, _ok())
            except S.SessionBusy:
                seen[label] = "busy"
        return {"n": 1}, {"status": "ok"}

    S.handle_request(db, account, session.session_id, "r1", "fp1", 1, work)
    assert seen == {"other": "busy", "same": "busy"}


def test_no_transaction_is_open_while_the_work_runs_so_other_writers_are_not_blocked(db, db_path):
    account, _ = _account(db)
    session = S.create_session(db, account)
    observed = {}

    def work(s):
        observed["in_transaction"] = db.in_transaction
        other = sqlite3.connect(db_path, timeout=0.5, isolation_level=None)
        try:
            other.execute("INSERT INTO accounts(account_id, name, created_at) VALUES ('a-x','x',0)")
            observed["other_writer"] = "ok"
        except sqlite3.OperationalError as e:            # "database is locked"
            observed["other_writer"] = str(e)
        finally:
            other.close()
        return {}, {"status": "ok"}

    S.handle_request(db, account, session.session_id, "r1", "fp", 1, work)
    assert observed == {"in_transaction": False, "other_writer": "ok"}


# ----------------------------------------------------------- failure + cancel

def test_a_request_whose_work_raises_has_no_effect_and_the_same_request_can_be_retried(db):
    account, _ = _account(db)
    session = S.create_session(db, account)

    def boom(s):
        raise RuntimeError("provider exploded")

    with pytest.raises(RuntimeError):
        S.handle_request(db, account, session.session_id, "r1", "fp", 1, boom)
    assert S.get_session(db, account, session.session_id).version == 1, "a failed request advanced the version"

    result, after, replayed = S.handle_request(db, account, session.session_id, "r1", "fp", 1, _ok({"n": 1}))
    assert replayed is False and after.version == 2


def test_a_cancelled_request_commits_nothing_and_a_retry_cannot_revive_it(db):
    account, _ = _account(db)
    session = S.create_session(db, account)
    ran = []

    def work(s):
        assert S.request_cancel(db, account, s.session_id) is True       # STOP arrives mid-work
        return {"n": 99}, {"status": "ok", "answer": "must never be delivered"}

    result, after, replayed = S.handle_request(db, account, session.session_id, "r1", "fp", 1, work)
    assert result == {"status": "cancelled"} and after.version == 1 and after.state == {}

    again, _, replayed = S.handle_request(db, account, session.session_id, "r1", "fp", 1,
                                          lambda s: ran.append(1) or ({}, {"status": "ok"}))
    assert again == {"status": "cancelled"} and replayed is True and ran == []


def test_cancel_with_nothing_running_says_so(db):
    account, _ = _account(db)
    session = S.create_session(db, account)
    assert S.request_cancel(db, account, session.session_id) is False


def test_a_session_that_changed_underneath_the_work_conflicts_at_commit(db):
    account, _ = _account(db)
    session = S.create_session(db, account)

    def work(s):
        db.execute("UPDATE sessions SET version=version+1 WHERE session_id=?", (s.session_id,))
        return {"n": 1}, {"status": "ok"}

    with pytest.raises(S.VersionConflict):
        S.handle_request(db, account, session.session_id, "r1", "fp", 1, work)
    assert S.get_session(db, account, session.session_id).state == {}, "stale work was committed"
