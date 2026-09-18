"""Production composition root for Netra's PostgreSQL worker pools."""

from __future__ import annotations

import asyncio
import os
import signal
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from netra_api.content.settings import ContentSettings
from netra_api.content.providers.pinecone import PineconeVectorIndex
from netra_api.content.providers.pymupdf import PyMuPDFDocumentParser
from netra_api.content.providers.s3 import Boto3ObjectStorage, LocalFixtureObjectStorage, ObjectStorageProvider
from netra_api.content.retrieval.embeddings import GeminiEmbeddingProvider
from netra_api.content.retrieval.chunks import AsyncSearchChunkRepository
from netra_api.content.reading.postgres import AsyncReadingBlockRepository
from netra_api.content.sources.postgres import AsyncSourceRepository
from netra_api.config import Settings
from netra_api.content.telemetry import configure_logging, configure_metrics
from netra_api.platform.database import create_engine, create_session_factory, dispose_engine
from netra_api.platform.tracing import DISABLED_TRACER, Tracer, build_tracer, disable_langsmith_export

from netra_worker.jobs.ingestion.activate_version import ActivateVersionJob, ActivateVersionPayload
from netra_worker.jobs.ingestion.build_blocks import BuildBlocksJob, BuildBlocksPayload, S3ParsedDocumentReader
from netra_worker.jobs.ingestion.embed_text import EmbedTextJob, EmbedTextPayload
from netra_worker.jobs.ingestion.parse_document import ParseDocumentJob, ParseDocumentPayload, S3ParsedDocumentStore
from netra_worker.jobs.search_projection.pinecone import SearchProjectionJob, SearchProjectionPayload
from netra_worker.runtime.dispatcher import JobHandler, WorkerPool, WorkerPoolConfig
from netra_worker.runtime.job_repository import Job
from netra_worker.runtime.postgres import AsyncJobRepository
from netra_worker.runtime.outbox_consumer import OutboxConsumer


class SessionScopedJobRepository:
    """Use an independent async session for every runtime database operation."""

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self.factory = factory

    async def claim_next(self, job_types, worker_id, lease_duration_seconds):
        async with self.factory() as session:
            return await AsyncJobRepository(session).claim_next(job_types, worker_id, lease_duration_seconds)

    async def complete(self, job_id, lease):
        async with self.factory() as session:
            await AsyncJobRepository(session).complete(job_id, lease)

    async def fail(self, job_id, lease, next_run_at, retryable=True):
        async with self.factory() as session:
            await AsyncJobRepository(session).fail(job_id, lease, next_run_at, retryable)

    async def heartbeat(self, job_id, lease, new_expires_at):
        async with self.factory() as session:
            return await AsyncJobRepository(session).heartbeat(job_id, lease, new_expires_at)

    async def enqueue(self, job_type, payload, idempotency_key):
        async with self.factory() as session:
            return await AsyncJobRepository(session).enqueue(job_type, payload, idempotency_key)


async def _with_session(factory: async_sessionmaker[AsyncSession], operation: Callable[[AsyncSession], Awaitable[None]]) -> None:
    async with factory() as session:
        await operation(session)


def _handlers(factory: async_sessionmaker[AsyncSession], settings: ContentSettings) -> dict[str, JobHandler]:
    storage: ObjectStorageProvider
    if settings.storage_provider == "local_fixture":
        if not settings.local_fixture_root:
            raise ValueError("local_fixture_root is required for local_fixture storage")
        storage = LocalFixtureObjectStorage(settings.local_fixture_root)
    elif settings.storage_provider == "s3":
        storage = Boto3ObjectStorage(settings)
    else:
        raise ValueError(f"unsupported storage_provider: {settings.storage_provider}")
    # Each stage enqueues its successor idempotently through the job table.
    scheduler = SessionScopedJobRepository(factory)
    parsed_store = S3ParsedDocumentStore(storage)
    parsed_reader = S3ParsedDocumentReader(storage)
    embedder = GeminiEmbeddingProvider(settings)
    index = PineconeVectorIndex(settings)

    async def parse(job: Job) -> None:
        await _with_session(factory, lambda session: ParseDocumentJob(
            AsyncSourceRepository(session), storage, parsed_store, PyMuPDFDocumentParser(),
            settings=settings, scheduler=scheduler,
        ).handle(ParseDocumentPayload.model_validate(job.payload)))

    async def build(job: Job) -> None:
        await _with_session(factory, lambda session: BuildBlocksJob(
            AsyncSourceRepository(session), parsed_reader, AsyncReadingBlockRepository(session),
            AsyncSearchChunkRepository(session), scheduler=scheduler,
        ).handle(BuildBlocksPayload.model_validate(job.payload)))

    async def embed(job: Job) -> None:
        await _with_session(factory, lambda session: EmbedTextJob(
            AsyncSourceRepository(session), AsyncSearchChunkRepository(session), embedder, settings,
            scheduler=scheduler,
        ).handle(EmbedTextPayload.model_validate(job.payload)))

    async def project(job: Job) -> None:
        await _with_session(factory, lambda session: SearchProjectionJob(
            AsyncSourceRepository(session), AsyncSearchChunkRepository(session), index, settings,
            scheduler=scheduler,
        ).handle(SearchProjectionPayload.model_validate(job.payload)))

    async def activate(job: Job) -> None:
        await _with_session(factory, lambda session: ActivateVersionJob(
            AsyncSourceRepository(session)
        ).handle(ActivateVersionPayload.model_validate(job.payload)))

    return {
        "parse_document": parse,
        "build_blocks": build,
        "embed_text": embed,
        "search_projection": project,
        "activate_version": activate,
    }


def build_pools(settings: ContentSettings, factory: async_sessionmaker[AsyncSession],
                tracer: Tracer = DISABLED_TRACER) -> list[WorkerPool]:
    handlers = _handlers(factory, settings)
    repository = SessionScopedJobRepository(factory)
    process = os.getpid()
    definitions = (
        ("parse", ("parse_document",), settings.parse_workers),
        ("blocks", ("build_blocks",), settings.block_workers),
        ("embed", ("embed_text",), settings.embed_workers),
        ("projection", ("search_projection",), settings.projection_workers),
        ("activation", ("activate_version",), settings.activation_workers),
    )
    return [WorkerPool(repository, handlers, WorkerPoolConfig(
        worker_id=f"{process}/{name}", job_types=job_types, concurrency=concurrency,
        lease_duration_seconds=settings.worker_lease_duration_seconds,
        poll_interval_seconds=settings.worker_poll_interval_seconds,
    ), tracer=tracer) for name, job_types, concurrency in definitions if concurrency > 0]


def _install_shutdown_handlers(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stop.set)
        except (NotImplementedError, RuntimeError):
            signal.signal(signum, lambda _signum, _frame: loop.call_soon_threadsafe(stop.set))


async def run() -> None:
    app_settings = Settings()
    settings = ContentSettings()
    if not app_settings.database_url:
        # Fail closed: the worker has no useful mode without its job table.
        raise SystemExit("NETRA_DATABASE_URL is required to run the worker")
    configure_logging(log_format=settings.log_format)
    configure_metrics(enabled=settings.metrics_enabled)
    disable_langsmith_export(os.environ)
    tracer = build_tracer(app_settings.tracing_mode, service_name="netra-worker")
    engine = create_engine(app_settings.database_url, pool_size=settings.database_pool_size)
    factory = create_session_factory(engine)
    stop = asyncio.Event()
    _install_shutdown_handlers(stop)
    pools = build_pools(settings, factory, tracer)
    outbox_consumer = OutboxConsumer(
        factory, f"{os.getpid()}/outbox", settings.worker_poll_interval_seconds,
        settings.outbox_lease_duration_seconds, tracer=tracer,
    )
    try:
        await asyncio.gather(*(pool.run(stop) for pool in pools), outbox_consumer.run(stop))
    finally:
        outbox_consumer.stop()
        for pool in pools:
            pool.stop()
        await dispose_engine(engine)
        # Bounded final flush outside any job attempt (never per job).
        await asyncio.to_thread(tracer.shutdown, app_settings.tracing_shutdown_timeout_seconds)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
