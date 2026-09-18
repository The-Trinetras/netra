"""Outbox dispatch registry: allow-listed handlers, poison events and fencing."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from netra_api.db.outbox import LEARNING_ATTEMPT_COMMITTED, SOURCE_VERSION_INGESTION_REQUESTED
from netra_worker.runtime.errors import LeaseLostError
from netra_worker.runtime.outbox import OutboxEvent
from netra_worker.runtime.outbox_consumer import OutboxConsumer


def _event(event_type, payload, *, aggregate_id=None, attempt_count=1):
    return OutboxEvent(event_id=uuid4(), aggregate_type="x", aggregate_id=aggregate_id or uuid4(),
                       event_type=event_type, payload=payload, created_at=datetime.now(timezone.utc),
                       attempt_count=attempt_count, claim_token=uuid4(), claim_worker_id="w")


class _Outbox:
    def __init__(self, fenced=False):
        self.processed, self.dead, self.fenced = [], [], fenced

    async def mark_processed(self, event_id, _at, _token, _worker):
        if self.fenced:
            raise LeaseLostError("claim lost")
        self.processed.append(event_id)

    async def mark_dead_lettered(self, event_id, _token, _worker, code):
        self.dead.append((event_id, code))


class _Jobs:
    def __init__(self, conflict=False):
        self.enqueued, self.conflict = [], conflict

    async def enqueue(self, job_type, payload, key):
        if self.conflict:
            raise ValueError("idempotency key is already used by a different job type")
        self.enqueued.append((job_type, payload, key))


def _consumer(jobs, **kwargs):
    return OutboxConsumer(lambda: None, "w", job_repository=lambda _session: jobs, **kwargs)


def _ingestion_payload(version=None):
    version = version or uuid4()
    return {"source_id": str(uuid4()), "source_version_id": str(version),
            "operation_key": f"parse_document:{version}", "job_type": "parse_document",
            "object_key": "k", "content_type": "application/pdf", "parsed_object_key": "p"}


def _learning_payload(attempt_id):
    return {"idempotency_key": f"learning.project_assessment_attempt:{attempt_id}",
            "account_id": str(uuid4()), "attempt_id": str(attempt_id), "concept_id": "ohms-law",
            "outcome": "correct", "hints_used": 0, "evaluated_by": "grader",
            "occurred_at": "2026-09-18T10:00:00+00:00"}


async def test_ingestion_event_creates_the_parse_job_before_acknowledging():
    jobs, outbox = _Jobs(), _Outbox()
    event = _event(SOURCE_VERSION_INGESTION_REQUESTED, _ingestion_payload())
    assert await _consumer(jobs)._dispatch(None, outbox, event) == "processed"
    [(job_type, payload, key)] = jobs.enqueued
    assert job_type == "parse_document" and key == payload.idempotency_key
    assert outbox.processed == [event.event_id]


async def test_learning_event_becomes_a_projection_job_keyed_by_the_attempt():
    jobs, outbox = _Jobs(), _Outbox()
    attempt = uuid4()
    event = _event(LEARNING_ATTEMPT_COMMITTED, _learning_payload(attempt), aggregate_id=attempt)
    assert await _consumer(jobs)._dispatch(None, outbox, event) == "processed"
    [(job_type, payload, key)] = jobs.enqueued
    assert job_type == "learning.project_assessment_attempt"
    assert key.endswith(str(attempt)) and payload.attempt_id == attempt


@pytest.mark.parametrize("make_event,code", [
    (lambda: _event(SOURCE_VERSION_INGESTION_REQUESTED, {"source_id": "not-a-uuid"}), "invalid_payload"),
    (lambda: _event(SOURCE_VERSION_INGESTION_REQUESTED, {**_ingestion_payload(), "job_type": "other"}),
     "unsafe_ingestion_payload"),
    (lambda: _event(LEARNING_ATTEMPT_COMMITTED, _learning_payload(uuid4())), "aggregate_mismatch"),
    (lambda: _event(LEARNING_ATTEMPT_COMMITTED, {**_learning_payload(uuid4()), "answer": "private"}),
     "invalid_payload"),
])
async def test_poison_events_are_dead_lettered_not_retried(make_event, code):
    jobs, outbox = _Jobs(), _Outbox()
    event = make_event()
    assert await _consumer(jobs)._dispatch(None, outbox, event) == "dead_lettered"
    assert outbox.dead == [(event.event_id, code)] and jobs.enqueued == []


async def test_unhandled_event_type_waits_then_dead_letters_after_bounded_claims():
    jobs, outbox = _Jobs(), _Outbox()
    consumer = _consumer(jobs, handlers={}, max_unhandled_attempts=3)
    early = _event(LEARNING_ATTEMPT_COMMITTED, {}, attempt_count=1)
    late = _event(LEARNING_ATTEMPT_COMMITTED, {}, attempt_count=3)
    assert await consumer._dispatch(None, outbox, early) == "unhandled"
    assert await consumer._dispatch(None, outbox, late) == "dead_lettered"
    assert outbox.dead == [(late.event_id, "unhandled_event_type")]


async def test_idempotency_conflict_is_dead_lettered():
    outbox = _Outbox()
    event = _event(SOURCE_VERSION_INGESTION_REQUESTED, _ingestion_payload())
    assert await _consumer(_Jobs(conflict=True))._dispatch(None, outbox, event) == "dead_lettered"
    assert outbox.dead == [(event.event_id, "idempotency_conflict")]


async def test_lost_claim_after_enqueue_is_not_an_error():
    jobs, outbox = _Jobs(), _Outbox(fenced=True)
    event = _event(SOURCE_VERSION_INGESTION_REQUESTED, _ingestion_payload())
    assert await _consumer(jobs)._dispatch(None, outbox, event) == "claim_lost"
    assert len(jobs.enqueued) == 1  # idempotent; the next claimant acknowledges


def test_handlers_must_be_allow_listed():
    with pytest.raises(ValueError, match="allow-listed"):
        OutboxConsumer(lambda: None, "w", handlers={"made.up": lambda event: None})
