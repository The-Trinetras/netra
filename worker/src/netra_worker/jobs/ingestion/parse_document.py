"""Worker stage that fetches, validates, parses, and stores PDF structure."""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Protocol

from netra_api.content.providers.llamaparse import ParsedBlock
from netra_api.content.providers.pymupdf import PdfParseStatus, PyMuPDFDocumentParser
from netra_api.content.providers.tesseract import TesseractOCRProvider
from netra_api.content.settings import ContentSettings
from netra_api.content.telemetry import instrument_stage
from netra_api.content.providers.s3 import ObjectStorageProvider

from netra_worker.jobs.ingestion.base import (
    IngestionJobPayload,
    IngestionVersionStore,
    StageScheduler,
    schedule_next,
    stage_key,
)
from netra_worker.runtime.errors import PermanentJobError


class ParseDocumentPayload(IngestionJobPayload):
    object_key: str
    content_type: str
    parsed_object_key: str | None = None


class ParsedDocumentStore(Protocol):
    async def put(self, key: str, blocks: list[ParsedBlock], content_hash: str) -> None: ...


class IngestionError(PermanentJobError):
    """Controlled, permanent failure that leaves the source version non-ready.

    Retrying cannot fix a hash mismatch, a malformed PDF or unreadable content,
    so the dispatcher dead-letters these instead of scheduling another attempt.
    """


class S3ParsedDocumentStore:
    def __init__(self, storage: ObjectStorageProvider) -> None:
        self.storage = storage

    async def put(self, key: str, blocks: list[ParsedBlock], content_hash: str) -> None:
        payload = {"content_hash": content_hash, "blocks": [block.model_dump(mode="json") for block in blocks]}
        await self.storage.put_object(key, json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(), "application/json")


class ParseDocumentJob:
    """Fetch, verify, parse and store one source version's PDF structure.

    Failure classes:
    - payload/identity mismatch: permanent; the version is left untouched;
    - hash mismatch, malformed or unreadable PDF: permanent; version FAILED;
    - storage or other transient errors: re-raised for retry; the version is
      NOT marked failed, because a later attempt can still succeed.
    A redelivery after the stage committed only re-schedules the next stage;
    it never re-parses, and can therefore never mark a ready/active version
    failed. CPU-bound parsing/OCR runs in a thread so lease renewal continues.
    """

    def __init__(self, versions: IngestionVersionStore, storage: ObjectStorageProvider,
                 parsed_documents: ParsedDocumentStore, parser: PyMuPDFDocumentParser | None = None,
                 ocr_provider: TesseractOCRProvider | None = None, settings: ContentSettings | None = None,
                 scheduler: StageScheduler | None = None) -> None:
        self.versions, self.storage, self.parsed_documents = versions, storage, parsed_documents
        self.parser = parser or PyMuPDFDocumentParser()
        config = settings or ContentSettings()
        self.ocr_provider = ocr_provider
        self.ocr_settings = config
        self.ocr_dpi = config.ocr_dpi
        self.scheduler = scheduler

    @instrument_stage("parse_pdf")
    async def handle(self, payload: ParseDocumentPayload) -> None:
        version = await self.versions.get_version_internal(payload.source_version_id)
        if version.source_id != payload.source_id or version.object_key != payload.object_key:
            raise IngestionError("source version identity does not match parse payload")
        key = payload.parsed_object_key or f"_netra/parsed/{payload.source_version_id}.json"
        if "parsing" in version.completed_stages:
            await self._schedule_blocks(payload, key)
            return
        if version.status.value == "failed":
            raise IngestionError("source version already failed ingestion")

        data = await self.storage.get_object(payload.object_key)  # transient errors retry
        actual_hash = hashlib.sha256(data).hexdigest()
        if actual_hash != version.content_hash:
            await self._fail(payload, "source content hash mismatch")
        result = await self._parse(payload, data)
        await self.parsed_documents.put(key, result.blocks, actual_hash)
        await self.versions.mark_stage_complete_internal(payload.source_version_id, "parsing")
        await self._schedule_blocks(payload, key)

    async def _parse(self, payload: ParseDocumentPayload, data: bytes):
        try:
            result = await asyncio.to_thread(self.parser.parse_bytes, data)
            if result.status == PdfParseStatus.OCR_REQUIRED:
                if not hasattr(self.parser, "parse_bytes_with_ocr"):
                    await self._fail(payload, "ocr_required but parser has no OCR-capable path")
                if self.ocr_provider is None:
                    self.ocr_provider = TesseractOCRProvider(
                        self.ocr_settings.tesseract_executable, self.ocr_settings.ocr_language,
                        self.ocr_settings.ocr_timeout_seconds)
                result = await asyncio.to_thread(
                    self.parser.parse_bytes_with_ocr, data, self.ocr_provider, dpi=self.ocr_dpi)
        except IngestionError:
            raise
        except Exception as exc:
            await self._fail(payload, f"PDF could not be parsed: {type(exc).__name__}", cause=exc)
        if result.status != PdfParseStatus.READY:
            await self._fail(payload, f"PDF parse did not produce ready content: {result.status}")
        return result

    async def _fail(self, payload: ParseDocumentPayload, reason: str, cause: Exception | None = None):
        await self.versions.mark_failed_internal(payload.source_version_id)
        raise IngestionError(reason) from cause

    async def _schedule_blocks(self, payload: ParseDocumentPayload, parsed_key: str) -> None:
        from netra_worker.jobs.ingestion.build_blocks import BuildBlocksPayload

        await schedule_next(self.scheduler, "build_blocks", BuildBlocksPayload(
            idempotency_key=stage_key("build_blocks", payload.source_version_id),
            source_id=payload.source_id, source_version_id=payload.source_version_id,
            parsed_object_key=parsed_key))
