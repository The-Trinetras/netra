"""Local, page-bounded Tesseract OCR provider.

The executable is selected only from trusted server settings.  OCR receives
rendered page bytes and returns provider-neutral words grouped into blocks;
this module performs no persistence, chunking, or activation.
"""

from __future__ import annotations

import math
import os
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

import pytesseract
from PIL import Image
from pydantic import BaseModel, Field
from pytesseract import Output


class OCRBlock(BaseModel):
    text: str
    page_index: int = Field(ge=0)
    bounding_box: tuple[float, float, float, float] | None = None
    confidence: float | None = Field(default=None, ge=0, le=100)
    extraction_method: str = "ocr"
    provider: str = "tesseract"
    language: str


class OCRProviderError(RuntimeError):
    """OCR was unavailable, timed out, or returned unusable output."""


class TesseractOCRProvider:
    def __init__(self, executable: str, language: str = "eng", timeout_seconds: float = 30.0) -> None:
        self.executable = executable
        self.language = language
        self.timeout_seconds = timeout_seconds
        if not Path(executable).is_file():
            raise OCRProviderError("configured Tesseract executable is unavailable")

    def ocr_page(self, image_bytes: bytes, *, page_index: int, page_width: float,
                 page_height: float) -> list[OCRBlock]:
        if not image_bytes or page_width <= 0 or page_height <= 0:
            raise OCRProviderError("invalid OCR page input")
        previous_command = pytesseract.pytesseract.tesseract_cmd
        pytesseract.pytesseract.tesseract_cmd = self.executable
        path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as stream:
                stream.write(image_bytes)
                path = stream.name
            with Image.open(path) as image:
                width, height = image.size
                if width <= 0 or height <= 0:
                    raise OCRProviderError("rendered OCR page has invalid dimensions")
                try:
                    data: dict[str, list[Any]] = pytesseract.image_to_data(
                        image, lang=self.language, output_type=Output.DICT,
                        config="--psm 3", timeout=self.timeout_seconds)
                except (RuntimeError, OSError) as exc:
                    raise OCRProviderError("Tesseract OCR request failed") from exc
            required_fields = ("text", "conf", "left", "top", "width", "height",
                               "block_num", "par_num", "line_num")
            if any(field not in data for field in required_fields):
                raise OCRProviderError("malformed Tesseract OCR output")
            if len({len(data[field]) for field in required_fields}) != 1:
                raise OCRProviderError("malformed Tesseract OCR output")
            scale_x, scale_y = page_width / width, page_height / height
            grouped: dict[tuple[int, int, int], list[tuple[str, float, float, float, float, float]]] = defaultdict(list)
            for i, raw_text in enumerate(data.get("text", [])):
                text = str(raw_text).strip()
                try:
                    confidence = float(data.get("conf", ["-1"])[i])
                    x, y = float(data["left"][i]), float(data["top"][i])
                    w, h = float(data["width"][i]), float(data["height"][i])
                except (KeyError, IndexError, TypeError, ValueError) as exc:
                    raise OCRProviderError("malformed Tesseract OCR output") from exc
                if not text or confidence < 0 or not all(math.isfinite(v) for v in (confidence, x, y, w, h)):
                    continue
                key = (int(data.get("block_num", [0])[i]), int(data.get("par_num", [0])[i]), int(data.get("line_num", [0])[i]))
                grouped[key].append((text, confidence, x, y, w, h))
            results: list[OCRBlock] = []
            for words in grouped.values():
                x0 = min(word[2] for word in words) * scale_x
                y0 = min(word[3] for word in words) * scale_y
                x1 = max(word[2] + word[4] for word in words) * scale_x
                y1 = max(word[3] + word[5] for word in words) * scale_y
                results.append(OCRBlock(text=" ".join(word[0] for word in words), page_index=page_index,
                    bounding_box=(round(x0, 3), round(y0, 3), round(x1, 3), round(y1, 3)),
                    confidence=sum(word[1] for word in words) / len(words), language=self.language))
            return results
        finally:
            pytesseract.pytesseract.tesseract_cmd = previous_command
            if path is not None:
                try:
                    os.unlink(path)
                except FileNotFoundError:
                    pass
