"""Worker stage that persists deterministic reading blocks and search chunks."""

from __future__ import annotations

import json
from typing import Protocol
from uuid import UUID

from netra_api.content.chunking import ChunkBlock, StructureAwareChunker
from netra_api.content.ingestion.structure import build_reading_blocks
from netra_api.content.providers.llamaparse import ParsedBlock
from netra_api.content.reading.blocks import ReadingBlock
from netra_api.content.retrieval.chunks import SearchChunk
from netra_api.content.telemetry import instrument_stage, stage_span, tracer_of

from netra_worker.runtime.errors import PermanentJobError
from netra_worker.jobs.ingestion.base import IngestionJobPayload, IngestionVersionStore


class BuildBlocksPayload(IngestionJobPayload):
    parsed_object_key: str


class ParsedDocumentReader(Protocol):
    async def get(self, key: str) -> tuple[str, list[ParsedBlock]]: ...


class ReadingBlockWriter(Protocol):
    async def replace_blocks(self, source_version_id: UUID, blocks: list[ReadingBlock]) -> None: ...


class SearchChunkWriter(Protocol):
    async def replace_chunks(self, source_version_id: UUID, chunks: list[SearchChunk]) -> None: ...


class S3ParsedDocumentReader:
    def __init__(self, storage) -> None:
        self.storage = storage

    async def get(self, key: str) -> tuple[str, list[ParsedBlock]]:
        payload = json.loads((await self.storage.get_object(key)).decode())
        return payload["content_hash"], [ParsedBlock.model_validate(item) for item in payload["blocks"]]


class BuildBlocksJob:
    def __init__(self, versions: IngestionVersionStore, parsed_documents: ParsedDocumentReader,
                 blocks: ReadingBlockWriter, chunks: SearchChunkWriter,
                 chunker: StructureAwareChunker | None = None) -> None:
        self.versions, self.parsed_documents, self.blocks, self.chunks = versions, parsed_documents, blocks, chunks
        self.chunker = chunker or StructureAwareChunker()

    @instrument_stage("build_reading_blocks_and_chunks")
    async def handle(self, payload: BuildBlocksPayload) -> None:
        version = await self.versions.get_version_internal(payload.source_version_id)
        if version.source_id != payload.source_id:
            raise PermanentJobError("job payload does not match the source version")
        if "blocks_built" in version.completed_stages:
            # Redelivery after this stage committed. Rebuilding would replace
            # chunks that later stages have already embedded and projected.
            return
        if version.ingestion_state.value != "parsing":
            raise PermanentJobError("source version is not ready for block construction")
        content_hash, parsed = await self.parsed_documents.get(payload.parsed_object_key)
        if content_hash != version.content_hash:
            raise RuntimeError("parsed document content hash mismatch")
        with stage_span(tracer_of(self), "ingestion.reading_blocks", operation="build_reading_blocks",
                        source_version_id=payload.source_version_id):
            reading_blocks = build_reading_blocks(payload.source_version_id, parsed)
        if not reading_blocks:
            raise RuntimeError("parsed document produced no reading blocks")
        await self.blocks.replace_blocks(payload.source_version_id, reading_blocks)
        with stage_span(tracer_of(self), "ingestion.search_chunks", operation="build_search_chunks",
                        source_version_id=payload.source_version_id):
            search_chunks = self.chunker.chunk([ChunkBlock.from_reading_block(block) for block in reading_blocks])
        await self.chunks.replace_chunks(payload.source_version_id, search_chunks)
        await self.versions.mark_stage_complete_internal(payload.source_version_id, "blocks_built")
