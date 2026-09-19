"""Durable jobs: leases, fenced writes, retries, checkpoints, and the worker around them.

Time is a fake clock wherever timing matters, so nothing here sleeps. Two tests use real time
(the heartbeat) because a heartbeat is a thread doing real work while a handler runs.
"""
from __future__ import annotations

import sqlite3
import threading
import time

import pytest
from pydantic import BaseModel, ValidationError

from demo.notes import jobs as J
from demo.notes import sessions as S
from slice.store import Store


class Clock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture
def path(tmp_path):
    p = str(tmp_path / "j.db")
    S.migrate(Store(p).db)
    return p


@pytest.fixture
def db(path):
    return Store(path).db


@pytest.fixture
def clock():
    return Clock()


def _worker(path, handlers, clock, **kw):
    kw.setdefault("heartbeat", False)
    kw.setdefault("backoff", J.ExponentialBackoff(base=10, jitter=0.0))
    return J.Worker(lambda: Store(path).db, handlers, now=clock, **kw)


def _add(db, clock, key="k1", job_type="t", **kw):
    return J.enqueue(db, job_type, key, {"n": 1}, now=clock(), **kw)[0]


# ------------------------------------------------------------------ enqueue

def test_the_same_idempotency_key_returns_the_existing_job_not_a_second_one(db, clock):
    first, created = J.enqueue(db, "t", "k", {"n": 1}, now=clock())
    again, created_again = J.enqueue(db, "t", "k", {"n": 2}, now=clock())
    assert (created, created_again, again) == (True, False, first)
    assert db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1


def test_the_same_key_for_a_different_job_type_is_a_different_job(db, clock):
    a, _ = J.enqueue(db, "a", "k", {}, now=clock())
    b, created = J.enqueue(db, "b", "k", {}, now=clock())
    assert created and a != b


# -------------------------------------------------------------------- claim

def test_a_claim_leases_the_job_counts_the_attempt_and_excludes_everyone_else(db, clock):
    _add(db, clock)
    job = J.claim_next(db, ["t"], "w1", 60, clock())
    assert job.attempts == 1 and job.worker_id == "w1" and job.payload == {"n": 1}
    assert J.claim_next(db, ["t"], "w2", 60, clock()) is None


def test_a_job_is_not_claimable_before_it_is_due_and_only_by_its_type(db, clock):
    J.enqueue(db, "t", "later", {}, now=clock() + 100)
    assert J.claim_next(db, ["t"], "w", 60, clock()) is None
    clock.advance(101)
    assert J.claim_next(db, ["other"], "w", 60, clock()) is None
    assert J.claim_next(db, ["t"], "w", 60, clock()) is not None


def test_jobs_are_claimed_in_due_order_then_creation_order(db, clock):
    J.enqueue(db, "t", "b", {"n": "second"}, now=clock() - 5)
    J.enqueue(db, "t", "a", {"n": "first"}, now=clock() - 10)
    assert J.claim_next(db, ["t"], "w", 60, clock()).payload == {"n": "first"}


# ------------------------------------------------------------------- leases

def test_an_expired_lease_lets_another_worker_take_over_and_the_old_owner_can_no_longer_write(db, clock):
    _add(db, clock)
    a = J.claim_next(db, ["t"], "wA", 60, clock())
    clock.advance(61)
    b = J.claim_next(db, ["t"], "wB", 60, clock())
    assert b is not None and b.attempts == 2
    with pytest.raises(J.LeaseLostError):
        J.complete(db, a, clock())
    J.complete(db, b, clock())
    assert J.describe(db, a.job_id)["status"] == "completed"


def test_a_heartbeat_extends_the_lease_so_nobody_can_take_the_job(db, clock):
    _add(db, clock)
    job = J.claim_next(db, ["t"], "wA", 60, clock())
    clock.advance(50)
    J.heartbeat(db, job, 60, clock())
    clock.advance(20)                                    # 70 s after the claim, but renewed at 50
    assert J.claim_next(db, ["t"], "wB", 60, clock()) is None


def test_a_job_whose_final_attempt_crashed_is_dead_lettered_not_claimed_forever(db, clock):
    _add(db, clock, max_attempts=1)
    J.claim_next(db, ["t"], "wA", 60, clock())           # the worker "crashes": never reports back
    clock.advance(61)
    assert J.claim_next(db, ["t"], "wB", 60, clock()) is None
    row = J.describe(db, db.execute("SELECT job_id FROM jobs").fetchone()[0])
    assert row["status"] == "dead_letter" and row["last_error"] == "lease expired after final attempt"


@pytest.mark.parametrize("write", [
    lambda db, job, c: J.complete(db, job, c),
    lambda db, job, c: J.cancel(db, job, c),
    lambda db, job, c: J.dead_letter(db, job, "x", c),
    lambda db, job, c: J.retry_later(db, job, 5, "x", c),
    lambda db, job, c: J.record_stage(db, job, "s", None, c),
    lambda db, job, c: J.heartbeat(db, job, 60, c),
], ids=["complete", "cancel", "dead_letter", "retry_later", "record_stage", "heartbeat"])
def test_every_write_is_fenced_by_the_lease(db, clock, write):
    _add(db, clock)
    real = J.claim_next(db, ["t"], "wA", 60, clock())
    import dataclasses
    for forged in (dataclasses.replace(real, lease_token="not-the-token"),
                   dataclasses.replace(real, worker_id="someone-else")):
        with pytest.raises(J.LeaseLostError):
            write(db, forged, clock())
    clock.advance(61)                                    # an expired lease is no lease
    with pytest.raises(J.LeaseLostError):
        write(db, real, clock())


# ------------------------------------------------------------ the worker

def test_a_successful_handler_completes_the_job(path, db, clock):
    job_id = _add(db, clock)
    calls = []
    outcome = _worker(path, {"t": lambda a: calls.append(a.job.payload)}, clock).run_once()
    assert outcome == "completed" and calls == [{"n": 1}]
    assert J.describe(db, job_id)["status"] == "completed"


def test_nothing_runnable_returns_none(path, clock):
    assert _worker(path, {"t": lambda a: None}, clock).run_once() is None


def test_a_failing_job_is_retried_later_with_backoff_and_then_succeeds(path, db, clock):
    job_id = _add(db, clock)
    state = {"fail": True}

    def handler(a):
        if state["fail"]:
            raise RuntimeError("the provider said PRIVATE-TEXT")

    worker = _worker(path, {"t": handler}, clock)
    assert worker.run_once() == "retry_scheduled"
    row = J.describe(db, job_id)
    assert row["status"] == "pending" and row["attempts"] == 1 and row["last_error"] == "RuntimeError"
    assert row["next_run_at"] == pytest.approx(clock() + 20)          # base 10 * 2**1, no jitter
    assert "PRIVATE-TEXT" not in str(dict(db.execute("SELECT * FROM jobs").fetchone()))

    assert worker.run_once() is None                                   # not due yet
    clock.advance(21)
    state["fail"] = False
    assert worker.run_once() == "completed"


def test_a_retry_skips_stages_that_already_finished_and_keeps_provider_ids(path, db, clock):
    _add(db, clock)
    seen = []

    def handler(a):
        seen.append((a.done("parse"), dict(a.job.remote_ops)))
        if not a.done("parse"):
            a.record_stage("parse", remote_op="provider-asset-42")
            raise RuntimeError("crashed after the provider call")

    worker = _worker(path, {"t": handler}, clock)
    assert worker.run_once() == "retry_scheduled"
    clock.advance(30)
    assert worker.run_once() == "completed"
    assert seen == [(False, {}), (True, {"parse": "provider-asset-42"})]


def test_a_job_that_keeps_failing_is_dead_lettered_after_its_attempts(path, db, clock):
    job_id = _add(db, clock, max_attempts=2)
    worker = _worker(path, {"t": lambda a: 1 / 0}, clock)
    assert worker.run_once() == "retry_scheduled"
    clock.advance(60)
    assert worker.run_once() == "dead_letter"
    row = J.describe(db, job_id)
    assert row["status"] == "dead_letter" and row["attempts"] == 2 and row["last_error"] == "ZeroDivisionError"


def test_a_permanent_error_is_dead_lettered_at_once_without_a_retry(path, db, clock):
    job_id = _add(db, clock)

    def handler(a):
        raise J.PermanentJobError("unsupported file")

    assert _worker(path, {"t": handler}, clock).run_once() == "dead_letter"
    assert J.describe(db, job_id)["attempts"] == 1


def test_an_invalid_payload_is_dead_lettered_not_retried(path, db, clock):
    class Payload(BaseModel):
        n: str

    def handler(a):
        Payload.model_validate({"n": 5})                 # a bad payload can never succeed

    _add(db, clock)
    assert _worker(path, {"t": handler}, clock).run_once() == "dead_letter"


def test_a_cancelled_job_is_terminal_and_never_run_again(path, db, clock):
    job_id = _add(db, clock)

    def handler(a):
        raise J.JobCancelled()

    worker = _worker(path, {"t": handler}, clock)
    assert worker.run_once() == "cancelled"
    clock.advance(10_000)
    assert worker.run_once() is None and J.describe(db, job_id)["status"] == "cancelled"


def test_a_worker_that_lost_its_lease_writes_nothing(path, db, clock):
    """A stalls; its lease expires; B takes the job; A then finishes. A must not mark it complete."""
    job_id = _add(db, clock)
    taken = {}

    def slow_handler(a):
        clock.advance(100)                               # A stalls past its lease
        taken["b"] = J.claim_next(Store(path).db, ["t"], "wB", 60, clock())

    outcome = _worker(path, {"t": slow_handler}, clock, worker_id="wA").run_once()
    row = J.describe(db, job_id)
    assert outcome == "lease_lost" and taken["b"].worker_id == "wB"
    assert row["status"] == "leased", "the stale worker overwrote the new owner's job"
    J.complete(db, taken["b"], clock())
    assert J.describe(db, job_id)["status"] == "completed"


def test_no_transaction_is_open_while_a_handler_runs(path, db, clock):
    _add(db, clock)
    seen = {}

    def handler(a):
        seen["in_transaction"] = a.db.in_transaction
        other = sqlite3.connect(path, timeout=0.3, isolation_level=None)
        try:
            other.execute("INSERT INTO accounts(account_id, name, created_at) VALUES ('x','x',0)")
            seen["other_writer"] = "ok"
        except sqlite3.OperationalError as e:
            seen["other_writer"] = str(e)
        finally:
            other.close()

    _worker(path, {"t": handler}, clock).run_once()
    assert seen == {"in_transaction": False, "other_writer": "ok"}


def test_a_database_error_while_claiming_does_not_kill_the_worker(clock):
    def broken():
        raise sqlite3.OperationalError("database is locked")

    worker = J.Worker(broken, {"t": lambda a: None}, now=clock, heartbeat=False)
    assert worker.run_once() == "error"


def test_the_worker_needs_handlers_and_positive_intervals():
    with pytest.raises(ValueError):
        J.Worker(lambda: None, {})
    with pytest.raises(ValueError):
        J.Worker(lambda: None, {"t": lambda a: None}, lease_seconds=0)


def test_run_forever_processes_jobs_and_stops_when_asked(path, db):
    J.enqueue(db, "t", "k", {}, now=time.time())
    done, stop = threading.Event(), threading.Event()
    worker = J.Worker(lambda: Store(path).db, {"t": lambda a: done.set()}, poll_seconds=0.05, heartbeat=False)
    thread = threading.Thread(target=worker.run_forever, args=(stop,))
    thread.start()
    assert done.wait(5)
    stop.set()
    thread.join(5)
    assert not thread.is_alive()


# ---------------------------------------------------------------- heartbeat

def test_a_heartbeat_keeps_a_long_job_from_being_taken_over(path, db):
    """Real time: the lease is 0.6 s, the handler takes 1.4 s. Without renewal another worker would
    take the job at 0.6 s; with it, nobody can and the job completes."""
    job_id = J.enqueue(db, "t", "k", {}, now=time.time())[0]
    stolen = []

    def handler(a):
        for _ in range(7):
            time.sleep(0.2)
            a.check()
            stolen.append(J.claim_next(Store(path).db, ["t"], "thief", 60))

    worker = J.Worker(lambda: Store(path).db, {"t": handler}, lease_seconds=0.6, worker_id="wA")
    assert worker.run_once() == "completed"
    assert stolen == [None] * 7 and J.describe(db, job_id)["status"] == "completed"


def test_a_handler_is_told_to_stop_when_its_lease_is_lost(path, db):
    job_id = J.enqueue(db, "t", "k", {}, now=time.time())[0]
    ran_to_the_end = []

    def handler(a):
        Store(path).db.execute("UPDATE jobs SET lease_token='taken-by-someone-else' WHERE job_id=?", (job_id,))
        for _ in range(20):
            time.sleep(0.1)
            a.check()                                    # raises once the heartbeat notices
        ran_to_the_end.append(True)                      # only reachable if check() never raised

    worker = J.Worker(lambda: Store(path).db, {"t": handler}, lease_seconds=0.45, worker_id="wA")
    started = time.time()
    assert worker.run_once() == "lease_lost"
    assert ran_to_the_end == [] and time.time() - started < 1.5, "the handler kept working without a lease"


# ------------------------------------------------------------------ backoff

def test_backoff_doubles_up_to_its_cap():
    b = J.ExponentialBackoff(base=1, max_delay=100, jitter=0.0)
    assert [b.next_delay(n) for n in range(9)] == [1, 2, 4, 8, 16, 32, 64, 100, 100]


def test_jitter_stays_within_its_ratio_and_spreads_retries():
    low = J.ExponentialBackoff(base=10, jitter=0.2, rng=lambda: 0.0).next_delay(0)
    mid = J.ExponentialBackoff(base=10, jitter=0.2, rng=lambda: 0.5).next_delay(0)
    high = J.ExponentialBackoff(base=10, jitter=0.2, rng=lambda: 1.0).next_delay(0)
    assert (low, mid, high) == (pytest.approx(8.0), pytest.approx(10.0), pytest.approx(12.0))


def test_backoff_rejects_nonsense_and_survives_a_huge_attempt_count():
    with pytest.raises(ValueError):
        J.ExponentialBackoff(jitter=1.5)
    with pytest.raises(ValueError):
        J.ExponentialBackoff().next_delay(-1)
    assert J.ExponentialBackoff(base=1, max_delay=60, jitter=0).next_delay(10**6) == 60
