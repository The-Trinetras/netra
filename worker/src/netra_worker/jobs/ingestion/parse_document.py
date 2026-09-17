"""Worker stage that fetches, validates, parses, and stores PDF structure."""

from __future__ import annotations

import hashlib
import json
from typing import Protocol

from netra_api.content.providers.llamaparse import ParsedBlock
from netra_api.content.providers.pymupdf import PdfParseStatus, PyMuPDFDocumentParser
from netra_api.content.providers.tesseract import TesseractOCRProvider
from netra_api.config import Settings
from netra_api.platform.observability import instrument_stage
from netra_api.content.providers.s3 import ObjectStorageProvider

from netra_worker.jobs.ingestion.base import IngestionJobPayload, IngestionVersionStore


class ParseDocumentPayload(IngestionJobPayload):
    object_key: str
    content_type: str
    parsed_object_key: str | None = None


class ParsedDocumentStore(Protocol):
    async def put(self, key: str, blocks: list[ParsedBlock], content_hash: str) -> None: ...


class IngestionError(RuntimeError):
    """Controlled failure that leaves the source version non-ready."""


class S3ParsedDocumentStore:
    def __init__(self, storage: ObjectStorageProvider) -> None:
        self.storage = storage

    async def put(self, key: str, blocks: list[ParsedBlock], content_hash: str) -> None:
        payload = {"content_hash": content_hash, "blocks": [block.model_dump(mode="json") for block in blocks]}
        await self.storage.put_object(key, json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(), "application/json")


class ParseDocumentJob:
    def __init__(self, versions: IngestionVersionStore, storage: ObjectStorageProvider,
                 parsed_documents: ParsedDocumentStore, parser: PyMuPDFDocumentParser | None = None,
                 ocr_provider: TesseractOCRProvider | None = None, settings: Settings | None = None) -> None:
        self.versions, self.storage, self.parsed_documents = versions, storage, parsed_documents
        self.parser = parser or PyMuPDFDocumentParser()
        config = settings or Settings()
        self.ocr_provider = ocr_provider
        self.ocr_settings = config
        self.ocr_dpi = config.ocr_dpi

    @instrument_stage("parse_pdf")
    async def handle(self, payload: ParseDocumentPayload) -> None:
        version = await self.versions.get_version_internal(payload.source_version_id)
        if version.source_id != payload.source_id or version.object_key != payload.object_key:
            raise IngestionError("source version identity does not match parse payload")
        try:
            data = await self.storage.get_object(payload.object_key)
            actual_hash = hashlib.sha256(data).hexdigest()
            if actual_hash != version.content_hash:
                raise IngestionError("source content hash mismatch")
            result = self.parser.parse_bytes(data)
            if result.status == PdfParseStatus.OCR_REQUIRED:
                if not hasattr(self.parser, "parse_bytes_with_ocr"):
                    raise IngestionError("ocr_required but parser has no OCR-capable path")
                if self.ocr_provider is None:
                    self.ocr_provider = TesseractOCRProvider(
                        self.ocr_settings.tesseract_executable, self.ocr_settings.ocr_language,
                        self.ocr_settings.ocr_timeout_seconds)
                result = self.parser.parse_bytes_with_ocr(data, self.ocr_provider, dpi=self.ocr_dpi)
            if result.status != PdfParseStatus.READY:
                raise IngestionError(f"PDF parse did not produce ready content: {result.status}")
            key = payload.parsed_object_key or f"_netra/parsed/{payload.source_version_id}.json"
            await self.parsed_documents.put(key, result.blocks, actual_hash)
            await self.versions.mark_stage_complete_internal(payload.source_version_id, "parsing")
        except Exception:
            await self.versions.mark_failed_internal(payload.source_version_id)
            raise
