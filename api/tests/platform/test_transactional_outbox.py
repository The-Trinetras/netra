"""API-callable outbox port: allow-list, caller transaction and deterministic identity."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from netra_api.db.outbox import (
    LEARNING_ATTEMPT_COMMITTED,
    OUTBOX_EVENT_TYPES,
    TransactionalOutbox,
    outbox_event_id,
)


class _Session:
    def __init__(self, in_transaction=True):
        self._in_transaction = in_transaction
        self.statements = []

    def in_transaction(self):
        return self._in_transaction

    async def execute(self, statement):
        self.statements.append(statement)


async def test_event_is_written_in_the_callers_transaction_with_a_deterministic_id():
    session = _Session()
    attempt = uuid4()
    payload = {"idempotency_key": f"learning.project_assessment_attempt:{attempt}"}
    first = await TransactionalOutbox().enqueue(session, event_type=LEARNING_ATTEMPT_COMMITTED,
                                                aggregate_id=attempt, payload=payload)
    again = await TransactionalOutbox().enqueue(session, event_type=LEARNING_ATTEMPT_COMMITTED,
                                                aggregate_id=attempt, payload=payload)
    assert first == again == outbox_event_id(LEARNING_ATTEMPT_COMMITTED, payload["idempotency_key"])
    compiled = str(session.statements[0].compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT (event_id) DO NOTHING" in compiled


async def test_writing_outside_a_transaction_is_refused():
    with pytest.raises(RuntimeError, match="inside the caller"):
        await TransactionalOutbox().enqueue(_Session(in_transaction=False), event_type=LEARNING_ATTEMPT_COMMITTED,
                                            aggregate_id=uuid4(), payload={"idempotency_key": "k"})


@pytest.mark.parametrize("event_type,payload", [
    ("made.up", {"idempotency_key": "k"}),
    (LEARNING_ATTEMPT_COMMITTED, {}),
    (LEARNING_ATTEMPT_COMMITTED, {"idempotency_key": "  "}),
])
async def test_unlisted_types_and_missing_keys_are_refused(event_type, payload):
    with pytest.raises(ValueError):
        await TransactionalOutbox().enqueue(_Session(), event_type=event_type, aggregate_id=uuid4(),
                                            payload=payload)


def test_the_worker_job_type_matches_m4s_projection_job():
    from netra_api.learning.graph.factual import LEARNING_PROJECTION_JOB_TYPE

    assert OUTBOX_EVENT_TYPES[LEARNING_ATTEMPT_COMMITTED].job_type == LEARNING_PROJECTION_JOB_TYPE
