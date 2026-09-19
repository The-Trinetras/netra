"""Ingestion stages schedule their successor, survive redelivery and classify failures."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from uuid import uuid4

import pymupdf
import pytest

from netra_api.content.retrieval.embeddings import EmbeddingResult, EmbeddingSpec
from netra_api.content.retrieval.chunks import SearchChunkProjection
from netra_api.content.settings import ContentSettings
from netra_api.content.sources.models import (
    SourceVersion,
    SourceVersionIngestionState as State,
    SourceVersionStatus as Status,
)
from netra_worker.jobs.ingestion.activate_version import ActivateVersionJob, ActivateVersionPayload
from netra_worker.jobs.ingestion.build_blocks import BuildBlocksJob, BuildBlocksPayload
from netra_worker.jobs.ingestion.embed_text import EmbedTextJob, EmbedTextPayload
from netra_worker.jobs.ingestion.parse_document import ParseDocumentJob, ParseDocumentPayload
from netra_worker.jobs.search_projection.pinecone import SearchProjectionJob, SearchProjectionPayload
from netra_worker.runtime.errors import PermanentJobError

STAGE_STATE = {"parsing": State.PARSING, "blocks_built": State.BLOCKS_BUILT,
               "embedded": State.EMBEDDED, "projected": State.PROJECTED}


def _pdf(text="Ohm's law relates voltage, current and resistance.") -> bytes:
    document = pymupdf.open()
    document.new_page().insert_text((72, 72), text)
    data = document.tobytes()
    document.close()
    return data


class Versions:
    """In-memory stand-in for the trusted worker paths of AsyncSourceRepository."""

    def __init__(self, data: bytes, *, active_number: int = 0):
        self.version = SourceVersion(
            source_version_id=uuid4(), source_id=uuid4(), version_number=active_number + 1,
            created_at=datetime.now(timezone.utc), object_key="source.pdf",
            content_hash=hashlib.sha256(data).hexdigest(), parser_name="pymupdf",
            parser_version="1.28.2")
        self.active_number = active_number
        self.failed_calls = 0

    async def get_version_internal(self, _id):
        return self.version

    async def mark_stage_complete_internal(self, _id, stage):
        if stage not in self.version.completed_stages:
            self.version.completed_stages.append(stage)
        self.version.ingestion_state, self.version.status = STAGE_STATE[stage], Status.PROCESSING
        return self.version

    async def mark_ready_internal(self, _id):
        self.version.ingestion_state, self.version.status = State.READY, Status.READY
        return self.version

    async def mark_failed_internal(self, _id):
        self.failed_calls += 1
        self.version.ingestion_state, self.version.status = State.FAILED, Status.FAILED

    async def active_version_number_internal(self, _source_id):
        return self.active_number


class Scheduler:
    def __init__(self):
        self.jobs = {}

    async def enqueue(self, job_type, payload, key):
        self.jobs.setdefault(key, (job_type, payload))  # idempotent like the job table


class Storage:
    def __init__(self, data=None, error=None):
        self.data, self.error = data, error

    async def get_object(self, _key):
        if self.error:
            raise self.error
        return self.data


class Parsed:
    def __init__(self):
        self.values = {}

    async def put(self, key, blocks, content_hash):
        self.values[key] = (content_hash, blocks)

    async def get(self, key):
        return self.values[key]


class Writer:
    def __init__(self):
        self.blocks, self.chunks = [], []

    async def replace_blocks(self, _id, blocks):
        self.blocks = blocks

    async def replace_chunks(self, _id, chunks):
        self.chunks = chunks

    async def list_for_embedding(self, _id):
        return self.chunks

    async def save_embedding(self, chunk_id, vector, spec):
        for chunk in self.chunks:
            if chunk.chunk_id == chunk_id:
                chunk.embedding, chunk.embedding_spec, chunk.embedding_version = vector, spec, spec.version


class Embedder:
    async def embed_batch(self, texts):
        spec = EmbeddingSpec(model="gemini-embedding-001", dimension=3, version="gemini-embedding-001:3")
        return [EmbeddingResult(vector=[0.1, 0.2, 0.3], specification=spec) for _ in texts]


class Index:
    def __init__(self):
        self.upserts = 0

    async def upsert(self, _namespace, _vectors):
        self.upserts += 1


def _parse_payload(versions):
    v = versions.version
    return ParseDocumentPayload(idempotency_key=f"parse_document:{v.source_version_id}", source_id=v.source_id,
                                source_version_id=v.source_version_id, object_key="source.pdf",
                                content_type="application/pdf", parsed_object_key="parsed.json")


async def test_each_stage_schedules_its_successor_through_to_activation():
    data = _pdf()
    versions, scheduler, parsed, writer = Versions(data, active_number=1), Scheduler(), Parsed(), Writer()
    settings = ContentSettings(gemini_embedding_dimension=3)
    version_id = versions.version.source_version_id

    await ParseDocumentJob(versions, Storage(data), parsed, scheduler=scheduler).handle(_parse_payload(versions))
    job_type, build = scheduler.jobs[f"build_blocks:{version_id}"]
    assert job_type == "build_blocks" and build.parsed_object_key == "parsed.json"

    await BuildBlocksJob(versions, parsed, writer, writer, scheduler=scheduler).handle(build)
    _, embed = scheduler.jobs[f"embed_text:{version_id}"]
    await EmbedTextJob(versions, writer, Embedder(), settings, scheduler=scheduler).handle(embed)
    _, project = scheduler.jobs[f"search_projection:{version_id}"]

    class Chunks:
        async def list_projection_chunks_for_ingestion(self, _id):
            return [SearchChunkProjection(
                chunk_id=chunk.chunk_id, source_version_id=version_id, source_id=versions.version.source_id,
                account_id=uuid4(), text=chunk.text, block_ids=chunk.block_ids,
                embedding_version=chunk.embedding_version, embedding=chunk.embedding,
                embedding_spec=chunk.embedding_spec, metadata=chunk.metadata) for chunk in writer.chunks]

    await SearchProjectionJob(versions, Chunks(), Index(), settings, scheduler=scheduler).handle(project)
    job_type, activate = scheduler.jobs[f"activate_version:{version_id}"]
    assert job_type == "activate_version"
    assert activate.expected_version_number == 1  # compare-and-set against the active version
    assert versions.version.status == Status.READY


async def test_redelivered_parse_of_an_active_version_never_reparses_or_fails_it():
    data = _pdf()
    versions, scheduler = Versions(data), Scheduler()
    versions.version.completed_stages = ["parsing", "blocks_built", "embedded", "projected"]
    versions.version.ingestion_state, versions.version.status = State.ACTIVE, Status.READY
    storage = Storage(error=AssertionError("a completed parse must not fetch source bytes again"))
    await ParseDocumentJob(versions, storage, Parsed(), scheduler=scheduler).handle(_parse_payload(versions))
    assert versions.failed_calls == 0 and versions.version.ingestion_state == State.ACTIVE
    assert len(scheduler.jobs) == 1  # the successor is re-ensured, idempotently


async def test_transient_storage_error_retries_without_failing_the_version():
    data = _pdf()
    versions = Versions(data)
    with pytest.raises(ConnectionError):
        await ParseDocumentJob(versions, Storage(error=ConnectionError("s3 timeout")), Parsed()).handle(
            _parse_payload(versions))
    assert versions.failed_calls == 0 and versions.version.status == Status.PENDING


async def test_hash_mismatch_is_permanent_and_fails_the_version():
    versions = Versions(_pdf())
    with pytest.raises(PermanentJobError):
        await ParseDocumentJob(versions, Storage(b"tampered"), Parsed()).handle(_parse_payload(versions))
    assert versions.version.status == Status.FAILED


async def test_a_failed_version_is_not_resurrected_by_a_later_attempt():
    data = _pdf()
    versions = Versions(data)
    versions.version.status, versions.version.ingestion_state = Status.FAILED, State.FAILED
    with pytest.raises(PermanentJobError):
        await ParseDocumentJob(versions, Storage(data), Parsed()).handle(_parse_payload(versions))
    assert versions.version.status == Status.FAILED


async def test_superseded_activation_is_permanent():
    from netra_api.content.sources.postgres import SourceVersionConflictError

    class Sources:
        async def activate_version_internal(self, *_):
            raise SourceVersionConflictError("cannot activate an obsolete source version")

    payload = ActivateVersionPayload(idempotency_key="activate", source_id=uuid4(), source_version_id=uuid4(),
                                     expected_version_number=1)
    with pytest.raises(PermanentJobError):
        await ActivateVersionJob(Sources()).handle(payload)
