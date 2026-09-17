"""Restore the locked real E3 fixture into an empty canonical environment.

The fixture source and version were created before ``SourceIngestionService``
introduced UUIDv5 identities; both locked identifiers are UUIDv4 values.  This
entrypoint therefore restores only those two historical canonical identity
rows, after validating the checked-in artifact and golden dataset.  Reading
blocks, search chunks, embeddings, Pinecone vectors, readiness, and activation
are all produced by the normal production handlers.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, NAMESPACE_URL, uuid5

from sqlalchemy import func, select

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "api" / "src"))
sys.path.insert(0, str(ROOT / "worker" / "src"))

from netra_api.config import Settings  # noqa: E402
from netra_api.content.chunking import ChunkBlock, StructureAwareChunker  # noqa: E402
from netra_api.content.ingestion.structure import build_reading_blocks  # noqa: E402
from netra_api.content.providers.llamaparse import ParsedBlock  # noqa: E402
from netra_api.content.providers.pinecone import PineconeVectorIndex  # noqa: E402
from netra_api.content.providers.s3 import Boto3ObjectStorage  # noqa: E402
from netra_api.content.reading.postgres import AsyncReadingBlockRepository  # noqa: E402
from netra_api.content.retrieval.chunks import AsyncSearchChunkRepository  # noqa: E402
from netra_api.content.retrieval.embeddings import GeminiEmbeddingProvider  # noqa: E402
from netra_api.content.sources.models import (  # noqa: E402
    SourceVersionIngestionState,
    SourceVersionStatus,
)
from netra_api.content.sources.postgres import AsyncSourceRepository  # noqa: E402
from netra_api.db.models import (  # noqa: E402
    ReadingBlockRow,
    SearchChunkRow,
    SourceRow,
    SourceVersionRow,
)
from netra_api.platform.database import create_engine, create_session_factory  # noqa: E402
from netra_worker.jobs.ingestion.activate_version import (  # noqa: E402
    ActivateVersionJob,
    ActivateVersionPayload,
)
from netra_worker.jobs.ingestion.build_blocks import (  # noqa: E402
    BuildBlocksJob,
    BuildBlocksPayload,
    S3ParsedDocumentReader,
)
from netra_worker.jobs.ingestion.embed_text import EmbedTextJob, EmbedTextPayload  # noqa: E402
from netra_worker.jobs.search_projection.pinecone import (  # noqa: E402
    SearchProjectionJob,
    SearchProjectionPayload,
)

SOURCE_ID = UUID("0f6a1857-7e1e-446c-9155-9e1bc1f2bd8a")
VERSION_ID = UUID("60610bd7-cd97-478e-84bd-82b13235b7ec")
ACCOUNT_ID = uuid5(NAMESPACE_URL, "netra:evaluation:account")
CONTENT_HASH = "537c872f1d2de7c9d338d08c90a436fbd724640ab4e43fbe4219c1f8fdb68935"
PDF = ROOT / "evaluation" / "fixtures" / "documents" / "Module 01 - Introduction to AI, GenAI, AI Ethics.pdf"
PARSED = ROOT / "evaluation" / "fixtures" / "_netra" / "parsed" / f"{VERSION_ID}.json"
DATASET = ROOT / "evaluation" / "cases" / "netra_e3_real_golden_v1.jsonl"
MANIFEST = ROOT / "evaluation" / "locked" / "netra_e3_real_golden_v1.manifest.json"
PDF_KEY = f"documents/{ACCOUNT_ID}/{SOURCE_ID}/{VERSION_ID}/module01.pdf"
PARSED_KEY = f"_netra/parsed/{VERSION_ID}.json"


def validate_locked_fixture() -> tuple[int, int, int, set[UUID]]:
    """Validate files and reproduce every locked chunk ID locally."""
    pdf_bytes = PDF.read_bytes()
    if hashlib.sha256(pdf_bytes).hexdigest() != CONTENT_HASH:
        raise RuntimeError("real evaluation PDF hash does not match the locked fixture")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    dataset_bytes = DATASET.read_bytes()
    if hashlib.sha256(dataset_bytes).hexdigest() != manifest["sha256"]:
        raise RuntimeError("golden dataset hash does not match its locked manifest")
    cases = [json.loads(line) for line in dataset_bytes.decode().splitlines() if line.strip()]
    golden = {UUID(chunk_id) for case in cases for chunk_id in case["reference_evidence_ids"]}
    if {UUID(item) for case in cases for item in case["source_ids"]} != {SOURCE_ID}:
        raise RuntimeError("golden dataset source identity is incompatible")
    if {UUID(item) for case in cases for item in case["source_version_ids"]} != {VERSION_ID}:
        raise RuntimeError("golden dataset source-version identity is incompatible")
    artifact = json.loads(PARSED.read_text(encoding="utf-8"))
    if artifact.get("content_hash") != CONTENT_HASH:
        raise RuntimeError("parsed artifact hash is incompatible")
    parsed = [ParsedBlock.model_validate(item) for item in artifact["blocks"]]
    blocks = build_reading_blocks(VERSION_ID, parsed)
    chunks = StructureAwareChunker().chunk([ChunkBlock.from_reading_block(block) for block in blocks])
    generated = {chunk.chunk_id for chunk in chunks}
    missing = golden - generated
    if missing:
        raise RuntimeError(f"locked golden chunk IDs were not reproduced: {len(missing)} missing")
    return len(parsed), len(blocks), len(chunks), golden


async def _put_artifacts(storage: Boto3ObjectStorage) -> None:
    await storage.put_object(PDF_KEY, PDF.read_bytes(), "application/pdf")
    await storage.put_object(PARSED_KEY, PARSED.read_bytes(), "application/json")


async def _ensure_historical_identity(factory) -> None:
    now = datetime.now(timezone.utc)
    async with factory() as session, session.begin():
        source = await session.get(SourceRow, SOURCE_ID)
        if source is None:
            session.add(SourceRow(source_id=SOURCE_ID, account_id=ACCOUNT_ID,
                                  title=PDF.stem, created_at=now))
        elif source.account_id != ACCOUNT_ID or source.title != PDF.stem:
            raise RuntimeError("existing canonical source conflicts with locked fixture")
        version = await session.get(SourceVersionRow, VERSION_ID)
        if version is None:
            session.add(SourceVersionRow(
                source_version_id=VERSION_ID,
                source_id=SOURCE_ID,
                version_number=1,
                status=SourceVersionStatus.PROCESSING.value,
                is_active=False,
                created_at=now,
                object_key=PDF_KEY,
                content_hash=CONTENT_HASH,
                parser_name="pymupdf",
                parser_version="1.28.2",
                parser_config={"artifact": "netra-e3-real-golden-v1", "ocr": True},
                ingestion_state=SourceVersionIngestionState.PARSING.value,
                completed_stages=["parsing"],
            ))
        elif (version.source_id != SOURCE_ID or version.version_number != 1
              or version.object_key != PDF_KEY or version.content_hash != CONTENT_HASH):
            raise RuntimeError("existing canonical source version conflicts with locked fixture")


async def _state(factory):
    async with factory() as session:
        return await AsyncSourceRepository(session).get_version_internal(VERSION_ID)


async def _run_pipeline(factory, settings: Settings) -> None:
    storage = Boto3ObjectStorage(settings)
    await _put_artifacts(storage)
    await _ensure_historical_identity(factory)

    version = await _state(factory)
    if "blocks_built" not in version.completed_stages:
        async with factory() as session:
            await BuildBlocksJob(
                AsyncSourceRepository(session),
                S3ParsedDocumentReader(storage),
                AsyncReadingBlockRepository(session),
                AsyncSearchChunkRepository(session),
            ).handle(BuildBlocksPayload(
                idempotency_key=f"build_blocks:{VERSION_ID}",
                source_id=SOURCE_ID,
                source_version_id=VERSION_ID,
                parsed_object_key=PARSED_KEY,
            ))

    version = await _state(factory)
    if "embedded" not in version.completed_stages:
        async with factory() as session:
            await EmbedTextJob(
                AsyncSourceRepository(session),
                AsyncSearchChunkRepository(session),
                GeminiEmbeddingProvider(settings),
                settings,
            ).handle(EmbedTextPayload(
                idempotency_key=f"embed_text:{VERSION_ID}",
                source_id=SOURCE_ID,
                source_version_id=VERSION_ID,
            ))

    version = await _state(factory)
    if "projected" not in version.completed_stages:
        async with factory() as session:
            await SearchProjectionJob(
                AsyncSourceRepository(session),
                AsyncSearchChunkRepository(session),
                PineconeVectorIndex(settings),
                settings,
            ).handle(SearchProjectionPayload(
                idempotency_key=f"search_projection:{VERSION_ID}",
                source_version_id=VERSION_ID,
            ))

    version = await _state(factory)
    if not version.is_active:
        async with factory() as session:
            await ActivateVersionJob(AsyncSourceRepository(session)).handle(
                ActivateVersionPayload(
                    idempotency_key=f"activate_version:{VERSION_ID}",
                    source_id=SOURCE_ID,
                    source_version_id=VERSION_ID,
                    expected_version_number=0,
                )
            )


async def main() -> None:
    parsed_count, block_count, chunk_count, golden = validate_locked_fixture()
    settings = Settings()
    if not settings.s3_bucket or not settings.aws_region:
        raise RuntimeError("S3_BUCKET and AWS_REGION are required")
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is required")
    if not settings.pinecone_api_key or not settings.pinecone_index_name:
        raise RuntimeError("Pinecone configuration is required")
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    try:
        await _run_pipeline(factory, settings)
        async with factory() as session:
            version = await AsyncSourceRepository(session).get_version_internal(VERSION_ID)
            reading = await session.scalar(select(func.count()).select_from(ReadingBlockRow).where(
                ReadingBlockRow.source_version_id == VERSION_ID))
            chunks = await session.scalar(select(func.count()).select_from(SearchChunkRow).where(
                SearchChunkRow.source_version_id == VERSION_ID))
            embedded = await session.scalar(select(func.count()).select_from(SearchChunkRow).where(
                SearchChunkRow.source_version_id == VERSION_ID,
                SearchChunkRow.embedding.is_not(None)))
            ids = set((await session.scalars(select(SearchChunkRow.chunk_id).where(
                SearchChunkRow.source_version_id == VERSION_ID))).all())
    finally:
        await engine.dispose()
    if not golden.issubset(ids):
        raise RuntimeError("canonical PostgreSQL is missing locked golden chunks")
    print(
        "fixture_restore=PASS "
        f"parsed_blocks={parsed_count} reading_blocks={reading}/{block_count} "
        f"search_chunks={chunks}/{chunk_count} embeddings={embedded} "
        f"golden_ids={len(golden)} state={version.ingestion_state.value} active={str(version.is_active).lower()}"
    )


if __name__ == "__main__":
    asyncio.run(main())
