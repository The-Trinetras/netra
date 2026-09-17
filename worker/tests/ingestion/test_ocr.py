from uuid import uuid4

import pytest

from netra_api.content.providers.pymupdf import PdfParseResult, PdfParseStatus
from netra_api.content.providers.llamaparse import ParsedBlock
from netra_worker.jobs.ingestion.parse_document import ParseDocumentJob, ParseDocumentPayload


@pytest.mark.asyncio
async def test_ocr_required_parse_continues_with_ocr_capable_parser():
    source_id, version_id = uuid4(), uuid4()
    data = b"pdf"

    class Version:
        object_key = "source.pdf"
        content_hash = __import__("hashlib").sha256(data).hexdigest()

    class Versions:
        def __init__(self):
            self.value = Version()
            self.value.source_id = source_id
        async def get_version_internal(self, _): return self.value
        async def mark_stage_complete_internal(self, *_): self.completed = True
        async def mark_failed_internal(self, *_): self.failed = True

    class Storage:
        async def get_object(self, _): return data

    class Parsed:
        async def put(self, key, blocks, content_hash): self.value = (key, blocks, content_hash)

    class Parser:
        def parse_bytes(self, _): return PdfParseResult(status=PdfParseStatus.OCR_REQUIRED, requires_ocr=True)
        def parse_bytes_with_ocr(self, _, _provider, *, dpi):
            assert dpi == 300
            return PdfParseResult(status=PdfParseStatus.READY,
                blocks=[ParsedBlock(text="OCR text", block_type_hint="paragraph", page_index=2,
                                    source_metadata={"extraction_method": "ocr"})])

    class Provider:
        pass

    versions, parsed = Versions(), Parsed()
    await ParseDocumentJob(versions, Storage(), parsed, Parser(), Provider()).handle(
        ParseDocumentPayload(idempotency_key="job", source_id=source_id, source_version_id=version_id,
                              object_key="source.pdf", content_type="application/pdf"))
    assert parsed.value[1][0].source_metadata["extraction_method"] == "ocr"
    assert versions.completed is True
    assert not hasattr(versions, "failed")
