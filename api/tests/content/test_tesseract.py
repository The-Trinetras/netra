from pathlib import Path

import pymupdf
import pytest
from PIL import Image

from netra_api.content.providers.pymupdf import PdfParseStatus, PyMuPDFDocumentParser
from netra_api.content.providers.tesseract import OCRProviderError, TesseractOCRProvider


def _png() -> bytes:
    image = Image.new("RGB", (200, 100), "white")
    import io
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


def test_missing_executable_is_rejected(tmp_path: Path):
    with pytest.raises(OCRProviderError, match="unavailable"):
        TesseractOCRProvider(str(tmp_path / "missing.exe"))


def test_tsv_words_are_grouped_and_mapped_to_pdf_coordinates(tmp_path: Path, monkeypatch):
    executable = tmp_path / "tesseract.exe"
    executable.touch()
    monkeypatch.setattr("pytesseract.image_to_data", lambda *args, **kwargs: {
        "text": ["hello", "world"], "conf": ["90", "80"],
        "left": ["10", "100"], "top": ["20", "20"], "width": ["40", "50"], "height": ["10", "10"],
        "block_num": ["1", "1"], "par_num": ["1", "1"], "line_num": ["1", "1"],
    })
    result = TesseractOCRProvider(str(executable)).ocr_page(_png(), page_index=3,
        page_width=100, page_height=50)
    assert len(result) == 1
    assert result[0].text == "hello world"
    assert result[0].page_index == 3
    assert result[0].bounding_box == (5.0, 10.0, 75.0, 15.0)
    assert result[0].confidence == 85
    assert result[0].extraction_method == "ocr"


def test_timeout_and_malformed_output_fail_closed(tmp_path: Path, monkeypatch):
    executable = tmp_path / "tesseract.exe"
    executable.touch()
    provider = TesseractOCRProvider(str(executable))
    monkeypatch.setattr("pytesseract.image_to_data", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("timeout")))
    with pytest.raises(OCRProviderError, match="failed"):
        provider.ocr_page(_png(), page_index=0, page_width=100, page_height=50)
    monkeypatch.setattr("pytesseract.image_to_data", lambda *args, **kwargs: {"text": ["x"], "conf": ["bad"]})
    with pytest.raises(OCRProviderError, match="malformed"):
        provider.ocr_page(_png(), page_index=0, page_width=100, page_height=50)


def test_missing_ocr_output_fields_are_malformed(tmp_path: Path, monkeypatch):
    executable = tmp_path / "tesseract.exe"
    executable.touch()
    provider = TesseractOCRProvider(str(executable))
    monkeypatch.setattr("pytesseract.image_to_data", lambda *args, **kwargs: {"text": []})
    with pytest.raises(OCRProviderError, match="malformed"):
        provider.ocr_page(_png(), page_index=0, page_width=100, page_height=50)


def test_mixed_native_and_image_pages_request_ocr_for_the_image_page():
    document = pymupdf.open()
    document.new_page().insert_text((72, 72), "Native page text.")
    image_page = document.new_page()
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, (0, 0, 16, 16), 0)
    pixmap.clear_with(255)
    image_page.insert_image(pymupdf.Rect(0, 0, 16, 16), pixmap=pixmap)
    pdf = document.tobytes()
    document.close()
    result = PyMuPDFDocumentParser().parse_bytes(pdf)
    assert result.status == PdfParseStatus.OCR_REQUIRED
    assert result.blocks[0].text == "Native page text."
