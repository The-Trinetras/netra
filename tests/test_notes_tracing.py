"""Traces: what is exported, what never is, and that a failing receiver cannot hurt an answer."""
from __future__ import annotations

import json

import pytest

from demo.notes import sessions as S
from demo.notes import tracing as T
from demo.notes.questions import by_id
from demo.notes.service import NotesService, ScriptedProvider
from slice.config import settings as load_settings

SECRET = "nt_" + "A" * 20


@pytest.fixture
def ran(tmp_path):
    """A real scripted ask, so the traces come from real run records."""
    svc = NotesService(str(tmp_path / "t.db"), ScriptedProvider(load_settings()))
    db = svc._open().db
    account = S.create_account(db, "asha")
    sid = svc.create_session(account).session_id
    svc.ask(account, sid, "req-1", by_id("q3-power").text, 1)
    return svc


def _all_runs(svc):
    store = svc._open()
    return store, [r["id"] for r in store.list_runs()]


def test_a_trace_has_a_span_per_exported_record_and_the_run_state(ran):
    store, runs = _all_runs(ran)
    notes = next(r for r in runs if store.meta(r) is not None and store.latest(r, "draft"))
    trace = T.run_to_trace(store, notes)
    assert [s["name"] for s in trace["spans"]] == ["input", "evidence", "draft", "verdict"]
    assert trace["state"] == "complete"
    assert all(s["end"] >= s["start"] for s in trace["spans"])


def test_private_answers_the_key_and_passage_text_are_never_in_a_trace(ran):
    store, runs = _all_runs(ran)
    key = next(store.latest(r, "answer_key") for r in runs if store.latest(r, "answer_key"))
    blob = ""
    for r in runs:
        blob += json.dumps(T.run_to_trace(store, r))
        blob += json.dumps(T.otlp_payload([T.run_to_trace(store, r)]))
    assert str(key["correct_answer"]) not in blob or len(str(key["correct_answer"])) < 4
    assert "answer_key" not in blob and "tutor_draft" not in blob and "accepted_answers" not in blob
    passage = next(p for r in runs if store.latest(r, "evidence") for p in store.latest(r, "evidence")["passages"])
    assert passage["text"] not in blob and f'{passage["doc"]}#{passage["ordinal"]}' in blob


def test_secrets_are_redacted_and_long_text_is_capped():
    assert SECRET not in T.scrub(f"my code is {SECRET} ok")
    assert "Bearer abc123" not in T.scrub("Authorization: Bearer abc123")
    assert T.scrub({"a": ["x" * 5000]})["a"][0].endswith("...") and len(T.scrub("x" * 5000)) < 700


def test_an_unknown_run_has_no_trace(ran):
    assert T.run_to_trace(ran._open(), "run_nope") is None


def test_the_otlp_payload_has_one_root_and_child_spans_linked_by_ids(ran):
    store, runs = _all_runs(ran)
    trace = T.run_to_trace(store, next(r for r in runs if store.latest(r, "draft")))
    spans = T.otlp_payload([trace])["resourceSpans"][0]["scopeSpans"][0]["spans"]
    root = spans[0]
    assert root["name"] == "notes.run" and "parentSpanId" not in root
    assert all(s["parentSpanId"] == root["spanId"] and s["traceId"] == root["traceId"] for s in spans[1:])
    assert len({s["spanId"] for s in spans}) == len(spans)


def _exporter(sent, fail=False, **kw):
    def send(payload):
        if fail:
            raise RuntimeError("receiver down")
        sent.append(payload)
    return T.Exporter(lambda r: {"trace_id": r, "state": "complete", "start": 1.0, "end": 2.0, "spans": []}, send, **kw)


def test_flush_sends_in_batches_and_counts():
    sent = []
    e = _exporter(sent, batch_size=2)
    for i in range(5):
        e.submit(f"run_{i}")
    assert e.flush() == 5 and len(sent) == 3 and e.stats() == {"queued": 0, "sent": 5, "failed": 0, "dropped": 0}


def test_a_failing_receiver_is_counted_and_never_raises():
    e = _exporter([], fail=True)
    e.submit("run_1")
    assert e.flush() == 0 and e.stats()["failed"] == 1


def test_a_full_queue_drops_the_oldest_and_says_so():
    sent = []
    e = _exporter(sent, max_queue=3, batch_size=10)
    for i in range(5):
        e.submit(f"run_{i}")
    assert e.stats()["dropped"] == 2
    e.flush()
    ids = [s["resourceSpans"][0]["scopeSpans"][0]["spans"] for s in sent][0]
    assert len(ids) == 3            # one root span per remaining trace


def test_a_loader_that_raises_is_a_counted_failure_not_a_crash():
    e = T.Exporter(lambda r: 1 / 0, lambda p: None)
    e.submit("run_1")
    e.flush()
    assert e.stats()["failed"] == 1


def test_tracing_is_off_without_both_keys_and_sends_the_headers_when_on():
    assert T.from_env(lambda r: None, {}) is None
    assert T.from_env(lambda r: None, {"ARIZE_SPACE_ID": "s"}) is None
    e = T.from_env(lambda r: None, {"ARIZE_SPACE_ID": "space1", "ARIZE_API_KEY": "key1"})
    assert e._send.headers == {"space_id": "space1", "api_key": "key1"} and e.project == "netra-notes"


def test_asking_queues_the_runs_and_a_broken_tracer_cannot_fail_the_answer(tmp_path):
    class Broken:
        def submit(self, run_id):
            raise RuntimeError("boom")
    good = _exporter([])
    for tracer in (good, Broken()):
        svc = NotesService(str(tmp_path / f"{type(tracer).__name__}.db"), ScriptedProvider(load_settings()), tracer=tracer)
        account = S.create_account(svc._open().db, "asha")
        sid = svc.create_session(account).session_id
        result, _, _ = svc.ask(account, sid, "req-1", by_id("q3-power").text, 1)
        assert result["status"] == "answered"
    assert good.stats()["queued"] == 2, "the notes run and the Tutor run should both be queued"
    svc.ask(account, sid, "req-1", by_id("q3-power").text, 2) if False else None


def test_asking_queues_the_runs_and_a_broken_tracer_cannot_fail_the_answer(tmp_path):
    class Broken:
        def submit(self, run_id):
            raise RuntimeError("boom")
    good = _exporter([])
    for name, tracer in (("good", good), ("broken", Broken())):
        svc = NotesService(str(tmp_path / f"{name}.db"), ScriptedProvider(load_settings()), tracer=tracer)
        account = S.create_account(svc._open().db, "asha")
        sid = svc.create_session(account).session_id
        result, _, _ = svc.ask(account, sid, "req-1", by_id("q3-power").text, 1)
        assert result["status"] == "answered"
    assert good.stats()["queued"] == 2, "the notes run and the Tutor run should both be queued"
