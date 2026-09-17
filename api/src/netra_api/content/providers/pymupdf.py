"""Page-aware local PDF parsing through PyMuPDF.

This adapter extracts text and layout evidence only. It does not perform OCR,
image understanding, equation recognition, or external provider calls.
"""

from __future__ import annotations

import hashlib
import re
from enum import StrEnum
from collections import Counter
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import fitz
from pydantic import BaseModel, Field

from netra_api.content.providers.llamaparse import ParsedBlock
from netra_api.content.providers.tesseract import OCRProviderError, TesseractOCRProvider

_LIST_RE = re.compile(r"^\s*(?:[\u2022\u25cf\u25e6]|[-*]|\d+[.)])\s+")


class PdfParseStatus(StrEnum):
    READY = "ready"
    EMPTY = "empty"
    OCR_REQUIRED = "ocr_required"


class PdfParseResult(BaseModel):
    status: PdfParseStatus
    blocks: list[ParsedBlock] = Field(default_factory=list)
    page_count: int = 0
    requires_ocr: bool = False
    source_metadata: dict[str, Any] = Field(default_factory=dict)


class PdfParseError(ValueError):
    """Raised when the input is not a readable PDF."""


class PyMuPDFDocumentParser:
    """Deterministically parse PDF bytes into page-aware parser blocks."""

    def parse_bytes(self, pdf_bytes: bytes) -> PdfParseResult:
        if not pdf_bytes:
            return PdfParseResult(status=PdfParseStatus.EMPTY)
        digest = hashlib.sha256(pdf_bytes).hexdigest()
        try:
            document = fitz.open(stream=pdf_bytes, filetype="pdf")
        except (fitz.FileDataError, RuntimeError, ValueError) as exc:
            raise PdfParseError("invalid PDF input") from exc

        with document:
            page_count = document.page_count
            if page_count == 0:
                return PdfParseResult(status=PdfParseStatus.EMPTY, source_metadata={"sha256": digest})
            style_sizes = self._style_sizes(document)
            body_size = Counter(style_sizes).most_common(1)[0][0] if style_sizes else 0.0
            heading_sizes = sorted({size for size in style_sizes if size >= body_size * 1.25}, reverse=True)
            blocks: list[ParsedBlock] = []
            parent_id = "document"
            parent_path: tuple[str, ...] = ()
            section_number = 0
            subsection_number = 0
            image_pages = 0
            text_pages = 0
            ocr_pages = 0

            for page in document:
                page_dict = page.get_text("dict", sort=True)
                page_has_text = False
                page_has_image = any(item.get("type") == 1 for item in page_dict.get("blocks", []))
                image_pages += page_has_image
                for raw in page_dict.get("blocks", []):
                    if raw.get("type") != 0:
                        continue
                    lines = raw.get("lines", [])
                    text = "\n".join(
                        "".join(span.get("text", "") for span in line.get("spans", [])).strip()
                        for line in lines
                    ).strip()
                    if not text:
                        continue
                    page_has_text = True
                    if _LIST_RE.match(text):
                        entries = [line.strip() for line in text.splitlines() if line.strip()]
                        if all(_LIST_RE.match(entry) for entry in entries):
                            for entry in entries:
                                blocks.append(self._block(entry, "list_item", page, raw, parent_id,
                                                          parent_path, digest))
                            continue
                    size = max((span.get("size", 0.0) for line in lines for span in line.get("spans", [])), default=0.0)
                    is_heading = bool(heading_sizes and size >= heading_sizes[-1] and size >= body_size * 1.25 and len(lines) <= 3)
                    if is_heading:
                        level = 1 if size >= heading_sizes[0] else 2
                        if level == 1:
                            section_number += 1
                            subsection_number = 0
                            parent_id = str(uuid5(uuid5(NAMESPACE_URL, digest), f"section:{section_number}:{text}"))
                            parent_path = (text,)
                        else:
                            subsection_number += 1
                            parent_id = str(uuid5(uuid5(NAMESPACE_URL, digest), f"subsection:{section_number}:{subsection_number}:{text}"))
                            parent_path = parent_path[:1] + (text,)
                        block_type = "heading"
                    else:
                        block_type = "paragraph"
                    blocks.append(self._block(text, block_type, page, raw, parent_id, parent_path, digest))
                text_pages += page_has_text
                if page_has_image and not page_has_text:
                    ocr_pages += 1

            metadata = {"sha256": digest, "parser": "pymupdf", "parser_version": fitz.VersionBind,
                        "native_pages": text_pages, "ocr_pages": ocr_pages}
            if ocr_pages:
                return PdfParseResult(status=PdfParseStatus.OCR_REQUIRED, blocks=blocks,
                                      page_count=page_count, requires_ocr=True, source_metadata=metadata)
            return PdfParseResult(status=PdfParseStatus.READY if blocks else PdfParseStatus.EMPTY,
                                  blocks=blocks, page_count=page_count, source_metadata=metadata)

    def parse_bytes_with_ocr(self, pdf_bytes: bytes, ocr_provider: TesseractOCRProvider,
                             *, dpi: int = 300) -> PdfParseResult:
        """Parse native pages and OCR only pages without usable native text."""
        native = self.parse_bytes(pdf_bytes)
        if native.status != PdfParseStatus.OCR_REQUIRED:
            return native
        digest = hashlib.sha256(pdf_bytes).hexdigest()
        try:
            document = fitz.open(stream=pdf_bytes, filetype="pdf")
        except (fitz.FileDataError, RuntimeError, ValueError) as exc:
            raise PdfParseError("invalid PDF input") from exc
        with document:
            native_by_page: dict[int, list[ParsedBlock]] = {}
            for block in native.blocks:
                if block.page_index is not None:
                    native_by_page.setdefault(block.page_index, []).append(block)
            blocks: list[ParsedBlock] = []
            ocr_page_count = 0
            for page in document:
                page_dict = page.get_text("dict", sort=True)
                has_native_text = any(item.get("type") == 0 and any(
                    "".join(span.get("text", "") for span in line.get("spans", [])).strip()
                    for line in item.get("lines", [])) for item in page_dict.get("blocks", []))
                if has_native_text:
                    blocks.extend(native_by_page.get(page.number, []))
                    continue
                ocr_page_count += 1
                pixmap = page.get_pixmap(dpi=dpi, alpha=False)
                try:
                    ocr_blocks = ocr_provider.ocr_page(pixmap.tobytes("png"), page_index=page.number,
                                                       page_width=page.rect.width, page_height=page.rect.height)
                except OCRProviderError:
                    raise
                if not ocr_blocks:
                    raise PdfParseError(f"OCR produced no usable text for page {page.number}")
                for ocr_block in ocr_blocks:
                    blocks.append(ParsedBlock(text=ocr_block.text, block_type_hint="paragraph",
                        page_index=ocr_block.page_index,
                        structured_location={"page_index": ocr_block.page_index,
                                             "bbox": list(ocr_block.bounding_box) if ocr_block.bounding_box else None,
                                             "extraction_method": "ocr"},
                        source_metadata={"sha256": digest, "parser": "pymupdf", "extraction_method": "ocr",
                                         "ocr_provider": ocr_block.provider, "ocr_language": ocr_block.language,
                                         "ocr_confidence": ocr_block.confidence}))
            if not blocks:
                raise PdfParseError("OCR produced no usable text")
            return PdfParseResult(status=PdfParseStatus.READY, blocks=blocks, page_count=document.page_count,
                                  source_metadata={"sha256": digest, "parser": "pymupdf", "ocr": True,
                                                   "ocr_pages": ocr_page_count})

    @staticmethod
    def _style_sizes(document: fitz.Document) -> list[float]:
        sizes: list[float] = []
        for page in document:
            for block in page.get_text("dict", sort=True).get("blocks", []):
                for line in block.get("lines", []):
                    sizes.extend(float(span.get("size", 0.0)) for span in line.get("spans", []))
        return [size for size in sizes if size > 0]

    @staticmethod
    def _block(text: str, block_type: str, page: fitz.Page, raw: dict[str, Any], parent_id: str,
               parent_path: tuple[str, ...], digest: str) -> ParsedBlock:
        bbox = [round(float(value), 3) for value in raw.get("bbox", (0, 0, 0, 0))]
        location = {"page_index": page.number, "bbox": bbox, "parent_id": parent_id,
                    "parent_path": list(parent_path)}
        return ParsedBlock(text=text, block_type_hint=block_type, page_index=page.number,
                           structured_location=location, parent_id=parent_id,
                           parent_path=parent_path, source_metadata={"sha256": digest,
                                                                      "extraction_method": "native"})
