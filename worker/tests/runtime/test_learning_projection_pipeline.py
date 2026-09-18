"""Committed attempt → outbox → durable job → projection handler, on real PostgreSQL.

Uses the real API learning store, the real outbox consumer, the real job
repository and the real WorkerPool. Only the Neo4j writer is a labelled
in-memory double (create-only by attempt_id, like the reviewed Cypher), so
this proves the durable pipeline and its failure classes, not Neo4j itself.
"""

import asyncio
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select, update

from netra_api.db.models import AssessmentAttemptRow, JobRow, OutboxRow, PendingQuestionRow
from netra_api.learning.assessment.models import AnswerSubmission, AssessmentAttempt, AttemptOutcome
from netra_api.learning.assessment.service import derive_attempt_id
from netra_api.learning.postgres import PostgresLearningStore
from netra_api.learning.quiz.models import AnswerKey, ApprovedQuestion, QuestionKind, QuestionOption
from netra_api.platform.auth_context import AuthContext
from netra_api.platform.database import create_engine, create_session_factory
from netra_worker.jobs.learning_projection.neo4j import LEARNING_PROJECTION_JOB_TYPE, ProjectionWrite
from netra_worker.main import SessionScopedJobRepository, learning_projection_handler
from netra_worker.runtime.dispatcher import WorkerPool, WorkerPoolConfig
from netra_worker.runtime.outbox_consumer import OutboxConsumer
from netra_worker.runtime.retries import ExponentialBackoffWithJitter

pytestmark = pytest.mark.integration


class GraphDouble:
    """LABELLED DOUBLE for Neo4j: one edge per attempt_id, properties set on create only."""

    def __init__(self, concepts=("ohms-law",), failures=0):
        self.concepts, self.failures, self.edges, self.calls = set(concepts), failures, {}, 0

    async def upsert_attempted(self, edge):
        self.calls += 1
        if self.failures:
            self.failures -= 1
            raise ConnectionError("injected projection outage")
        if edge.concept_id not in self.concepts:
            return ProjectionWrite.CONCEPT_NOT_PROJECTED
        self.edges.setdefault(edge.attempt_id, edge)
        return ProjectionWrite.PROJECTED


@pytest.fixture
async def env():
    url = os.environ.get("NETRA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("NETRA_TEST_DATABASE_URL (a disposable local database) is not set")
    engine = create_engine(url)
    sessions = create_session_factory(engine)
    questions: list[str] = []
    yield sessions, questions
    async with sessions() as session, session.begin():
        attempt_ids = select(AssessmentAttemptRow.attempt_id).where(AssessmentAttemptRow.question_id.in_(questions))
        await session.execute(delete(JobRow).where(JobRow.operation_key.in_(
            select(func.concat(f"{LEARNING_PROJECTION_JOB_TYPE}:", AssessmentAttemptRow.attempt_id))
            .where(AssessmentAttemptRow.question_id.in_(questions)))))
        await session.execute(delete(OutboxRow).where(OutboxRow.aggregate_id.in_(attempt_ids)))
        await session.execute(delete(AssessmentAttemptRow).where(AssessmentAttemptRow.question_id.in_(questions)))
        await session.execute(delete(PendingQuestionRow).where(PendingQuestionRow.question_id.in_(questions)))
    await engine.dispose()


async def _commit(sessions, questions, concept="ohms-law", when=None):
    auth = AuthContext(account_id=uuid4(), session_id=uuid4(), request_id=uuid4(), issued_at=datetime.now(timezone.utc))
    store = PostgresLearningStore(sessions)
    question_id = f"q-{uuid4()}"
    questions.append(question_id)
    await store.persist_pending(auth, ApprovedQuestion(
        question_id=question_id, question_version=1, concept_id=concept, kind=QuestionKind.TRUE_FALSE,
        prompt="Is R constant?", options=[QuestionOption(option_id="t", text="True"), QuestionOption(option_id="f", text="False")],
        answer_key=AnswerKey(correct_answer="t"), created_at=datetime.now(timezone.utc)))
    return await store.commit_answer(auth, AssessmentAttempt(
        attempt_id=derive_attempt_id(auth.request_id, question_id, 1), account_id=auth.account_id, concept_id=concept,
        question_id=question_id, question_version=1, answer=AnswerSubmission(final_text="True"),
        outcome=AttemptOutcome.CORRECT, hints_used=0, evaluated_by="grader",
        created_at=when or datetime.now(timezone.utc)))


async def _drain_outbox(sessions, attempts):
    consumer = OutboxConsumer(sessions, "test/outbox", poll_interval_seconds=0.01, lease_duration_seconds=30)
    ids = {a.attempt_id for a in attempts}
    for _ in range(50):
        await consumer.poll_once()
        async with sessions() as session:
            pending = (await session.execute(select(func.count()).select_from(OutboxRow).where(
                OutboxRow.aggregate_id.in_(ids), OutboxRow.processed_at.is_(None)))).scalar_one()
        if not pending:
            return
    raise AssertionError("outbox events were not processed")


async def _jobs(sessions, attempts):
    keys = [f"{LEARNING_PROJECTION_JOB_TYPE}:{a.attempt_id}" for a in attempts]
    async with sessions() as session:
        return (await session.execute(select(JobRow).where(JobRow.operation_key.in_(keys)))).scalars().all()


async def _run_pool(sessions, writer, until, *, max_seconds=10):
    pool = WorkerPool(SessionScopedJobRepository(sessions), {LEARNING_PROJECTION_JOB_TYPE: learning_projection_handler(writer)},
                      WorkerPoolConfig(worker_id="test-projection", job_types=(LEARNING_PROJECTION_JOB_TYPE,),
                                       poll_interval_seconds=0.02, lease_duration_seconds=5),
                      backoff=ExponentialBackoffWithJitter(base_delay=timedelta(milliseconds=10),
                                                           max_delay=timedelta(milliseconds=20), jitter_ratio=0))
    stop = asyncio.Event()

    async def watch():
        while not await until():
            await asyncio.sleep(0.02)
        stop.set()

    await asyncio.wait_for(asyncio.gather(pool.run(stop), watch()), max_seconds)


async def test_a_committed_attempt_is_projected_once_even_when_the_event_is_redelivered(env):
    sessions, questions = env
    attempt = await _commit(sessions, questions)
    await _drain_outbox(sessions, [attempt])
    # Crash between job creation and acknowledgement: the event is delivered again.
    async with sessions() as session, session.begin():
        await session.execute(update(OutboxRow).where(OutboxRow.aggregate_id == attempt.attempt_id)
                              .values(processed_at=None, claim_token=None, claim_worker_id=None, claim_until=None))
    await _drain_outbox(sessions, [attempt])
    jobs = await _jobs(sessions, [attempt])
    assert len(jobs) == 1 and jobs[0].payload["attempt_id"] == str(attempt.attempt_id)
    assert "True" not in str(jobs[0].payload)  # the student's answer text never leaves PostgreSQL

    graph = GraphDouble()

    async def done():
        return (await _jobs(sessions, [attempt]))[0].status == "completed"

    await _run_pool(sessions, graph, done)
    assert list(graph.edges) == [attempt.attempt_id] and graph.calls == 1


async def test_a_projection_outage_retries_and_never_erases_the_committed_attempt(env):
    sessions, questions = env
    attempt = await _commit(sessions, questions)
    await _drain_outbox(sessions, [attempt])
    graph = GraphDouble(failures=2)

    async def done():
        return (await _jobs(sessions, [attempt]))[0].status == "completed"

    await _run_pool(sessions, graph, done)
    job = (await _jobs(sessions, [attempt]))[0]
    assert job.attempts == 3 and graph.calls == 3 and attempt.attempt_id in graph.edges
    assert await PostgresLearningStore(sessions).get(
        AuthContext(account_id=attempt.account_id, session_id=uuid4(), request_id=uuid4(),
                    issued_at=datetime.now(timezone.utc)), attempt.attempt_id) == attempt


async def test_a_missing_concept_dead_letters_visibly_after_bounded_attempts(env):
    sessions, questions = env
    attempt = await _commit(sessions, questions, concept="uncurated-concept")
    await _drain_outbox(sessions, [attempt])
    graph = GraphDouble(concepts=())

    async def dead():
        return (await _jobs(sessions, [attempt]))[0].status == "dead_letter"

    await _run_pool(sessions, graph, dead)
    job = (await _jobs(sessions, [attempt]))[0]
    assert job.attempts == job.max_attempts and graph.edges == {}
    async with sessions() as session:
        assert await session.get(AssessmentAttemptRow, attempt.attempt_id) is not None


async def test_an_older_event_processed_last_cannot_overwrite_a_newer_attempt(env):
    sessions, questions = env
    now = datetime.now(timezone.utc)
    older = await _commit(sessions, questions, when=now - timedelta(minutes=5))
    newer = await _commit(sessions, questions, when=now)
    await _drain_outbox(sessions, [older, newer])
    graph = GraphDouble()
    # Deliver the newer projection first, then replay the older one twice.
    handler = learning_projection_handler(graph)
    jobs = {job.payload["attempt_id"]: job for job in await _jobs(sessions, [older, newer])}
    for attempt_id in (str(newer.attempt_id), str(older.attempt_id), str(older.attempt_id)):
        await handler(_as_job(jobs[attempt_id]))
    assert graph.edges[newer.attempt_id].occurred_at == newer.created_at
    assert graph.edges[older.attempt_id].occurred_at == older.created_at and len(graph.edges) == 2


def _as_job(row):
    from netra_worker.runtime.job_repository import Job

    return Job(job_id=row.job_id, job_type=row.job_type, payload=row.payload, next_run_at=row.next_run_at,
               created_at=row.created_at, updated_at=row.updated_at)
