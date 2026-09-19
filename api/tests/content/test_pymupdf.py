import pymupdf
import pytest
from uuid import uuid4

from netra_api.content.chunking import ChunkBlock, StructureAwareChunker
from netra_api.content.ingestion.structure import build_reading_blocks
from netra_api.content.providers.pymupdf import PdfParseError, PdfParseStatus, PyMuPDFDocumentParser


def _pdf(*pages: list[tuple[str, float, tuple[float, float]]]) -> bytes:
    document = pymupdf.open()
    for page_items in pages:
        page = document.new_page()
        y = 72
        for text, size, position in page_items:
            page.insert_text((position[0], y + position[1]), text, fontsize=size)
            y += size + 18
    result = document.tobytes()
    document.close()
    return result


def test_extracts_multiple_pages_in_page_and_block_order():
    result = PyMuPDFDocumentParser().parse_bytes(_pdf(
        [("First paragraph.", 11, (72, 0)), ("Second paragraph.", 11, (72, 0))],
        [("Third paragraph.", 11, (72, 0))],
    ))
    assert result.status == PdfParseStatus.READY
    assert [block.page_index for block in result.blocks] == [0, 0, 1]
    assert [block.text for block in result.blocks] == ["First paragraph.", "Second paragraph.", "Third paragraph."]


def test_heading_layout_propagates_section_and_subsection_parents_deterministically():
    pdf = _pdf(
        [("Section One", 20, (72, 0)), ("Section body.", 11, (72, 0)),
         ("Subsection One", 15, (72, 0)), ("Subsection body.", 11, (72, 0))],
    )
    first = PyMuPDFDocumentParser().parse_bytes(pdf)
    second = PyMuPDFDocumentParser().parse_bytes(pdf)
    assert [block.model_dump() for block in first.blocks] == [block.model_dump() for block in second.blocks]
    assert first.blocks[0].block_type_hint == "heading"
    assert first.blocks[1].parent_path == ("Section One",)
    assert first.blocks[3].parent_path == ("Section One", "Subsection One")

    reading = build_reading_blocks(uuid4(), first.blocks)
    chunks = StructureAwareChunker().chunk([ChunkBlock.from_reading_block(block) for block in reading])
    assert chunks
    assert all(chunk.source_version_id == reading[0].source_version_id for chunk in chunks)
    assert all(chunk.block_ids for chunk in chunks)


def test_empty_and_malformed_documents_are_distinguished():
    assert PyMuPDFDocumentParser().parse_bytes(b"").status == PdfParseStatus.EMPTY
    with pytest.raises(PdfParseError):
        PyMuPDFDocumentParser().parse_bytes(b"not a PDF")


def test_truncated_pdf_fails_as_a_controlled_parse_error():
    pdf = _pdf([("Text before truncation.", 11, (72, 0))])
    with pytest.raises(PdfParseError):
        PyMuPDFDocumentParser().parse_bytes(pdf[: len(pdf) // 2])


def test_image_only_document_requires_ocr_without_fabricating_text():
    document = pymupdf.open()
    page = document.new_page()
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, (0, 0, 16, 16), 0)
    pixmap.clear_with(255)
    page.insert_image(pymupdf.Rect(0, 0, 16, 16), pixmap=pixmap)
    pdf = document.tobytes()
    document.close()

    result = PyMuPDFDocumentParser().parse_bytes(pdf)
    assert result.status == PdfParseStatus.OCR_REQUIRED
    assert result.requires_ocr is True
    assert result.blocks == []


def test_page_location_is_retained_by_reading_blocks():
    version_id = uuid4()
    parsed = PyMuPDFDocumentParser().parse_bytes(_pdf([("Located text.", 11, (72, 0))])).blocks
    blocks = build_reading_blocks(version_id, parsed)
    assert blocks[0].page_index == 0
    assert blocks[0].structured_location["page_index"] == 0
    assert blocks[0].source_version_id == version_id


def test_mixed_native_and_ocr_output_remains_in_page_order_and_has_provenance():
    document = pymupdf.open()
    document.new_page().insert_text((72, 72), "Native page text.")
    image_page = document.new_page()
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, (0, 0, 16, 16), 0)
    pixmap.clear_with(255)
    image_page.insert_image(pymupdf.Rect(0, 0, 16, 16), pixmap=pixmap)
    document.new_page().insert_text((72, 72), "Later native page text.")
    pdf = document.tobytes()
    document.close()

    class OCR:
        def ocr_page(self, _image, *, page_index, page_width, page_height):
            from netra_api.content.providers.tesseract import OCRBlock
            return [OCRBlock(text="OCR page text", page_index=page_index,
                             bounding_box=(1, 2, 3, 4), confidence=91, language="eng")]

    result = PyMuPDFDocumentParser().parse_bytes_with_ocr(pdf, OCR())
    assert [block.page_index for block in result.blocks] == [0, 1, 2]
    assert [block.source_metadata["extraction_method"] for block in result.blocks] == ["native", "ocr", "native"]
    assert result.blocks[1].structured_location["bbox"] == [1.0, 2.0, 3.0, 4.0]
    assert result.blocks[1].source_metadata["ocr_confidence"] == 91.0


def test_mixed_document_with_empty_ocr_page_fails_closed():
    document = pymupdf.open()
    document.new_page().insert_text((72, 72), "Native page text.")
    image_page = document.new_page()
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, (0, 0, 16, 16), 0)
    pixmap.clear_with(255)
    image_page.insert_image(pymupdf.Rect(0, 0, 16, 16), pixmap=pixmap)
    pdf = document.tobytes()
    document.close()

    class EmptyOCR:
        def ocr_page(self, *_args, **_kwargs):
            return []

    with pytest.raises(PdfParseError, match="page 1"):
        PyMuPDFDocumentParser().parse_bytes_with_ocr(pdf, EmptyOCR())


def test_no_module_uses_the_deprecated_fitz_alias():
    """C-open-4: PyMuPDF's ``fitz`` name is a deprecated alias; use ``pymupdf``."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    pattern = re.compile(r"^\s*(import fitz\b|from fitz\b)", re.MULTILINE)
    offenders = [
        str(path.relative_to(root))
        for folder in ("api", "worker", "evaluation", "tests")
        for path in (root / folder).rglob("*.py")
        if ".venv" not in path.parts and pattern.search(path.read_text(encoding="utf-8", errors="ignore"))
    ]
    assert offenders == []
