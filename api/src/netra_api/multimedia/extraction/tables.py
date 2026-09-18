"""Convert a parser's Markdown table into a TableStructure.

The approved document parser (LlamaParse, behind M2's
DocumentParserProvider) returns tables as Markdown. This converter is
deterministic: it keeps every cell's exact text, reads units only from an
explicit "(unit)" or "[unit]" suffix in a header or cell, and parses a
number only when the whole cell (minus an explicit unit) is a number.
Nothing is inferred, so what reaches validation is what the parser said.

Row 0 is the header row, matching how a reader meets the table and the
existing tbl01 fixture. Empty data cells produce no cell, so validation
reports "present in source, missing in extraction" instead of an empty
string that looks like a value.

HTML tables (merged cells via rowspan/colspan) are not parsed here: the
parser's Markdown form cannot express spans, and guessing them would
detach cells from their headers. They raise ExtractionUnsupportedError.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple
from uuid import UUID

from netra_api.multimedia.providers.errors import (
    ExtractionUnsupportedError,
    MalformedProviderResponseError,
)
from netra_api.multimedia.tables.models import (
    HeaderOrientation,
    TableCell,
    TableEvidenceReference,
    TableHeader,
    TableStructure,
)

EXTRACTION_PROVIDER = "document_parser"

_SEPARATOR = re.compile(r"^:?-{3,}:?$")
_UNIT_SUFFIX = re.compile(r"^(?P<text>.*?)\s*[\(\[](?P<unit>[^()\[\]]{1,16})[\)\]]\s*$")
_NUMBER_WITH_UNIT = re.compile(r"^(?P<number>[+\-−]?(?:\d+(?:\.\d+)?|\.\d+))(?:\s*(?P<unit>[A-Za-zΩµ%]{1,8}))?$")


def looks_like_markdown_table(text: str) -> bool:
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    return len(lines) >= 2 and lines[0].startswith("|") and _is_separator_row(lines[1])


def _split_row(line: str) -> List[str]:
    line = line.strip()
    if not (line.startswith("|") and line.endswith("|")):
        raise MalformedProviderResponseError(EXTRACTION_PROVIDER, "table row is not pipe-delimited", field="table")
    return [cell.strip() for cell in line[1:-1].split("|")]


def _is_separator_row(line: str) -> bool:
    try:
        cells = _split_row(line)
    except MalformedProviderResponseError:
        return False
    return bool(cells) and all(_SEPARATOR.match(cell.replace(" ", "")) for cell in cells)


def split_unit(text: str) -> Tuple[str, Optional[str]]:
    """'Current (A)' -> ('Current', 'A'); text without an explicit unit is unchanged."""

    match = _UNIT_SUFFIX.match(text)
    if match and match.group("text").strip():
        return match.group("text").strip(), match.group("unit").strip()
    return text, None


def parse_number(text: str) -> Tuple[Optional[float], Optional[str]]:
    """'2' -> (2.0, None); '2 V' -> (2.0, 'V'); '−2' -> (-2.0, None); 'about 2' -> (None, None)."""

    match = _NUMBER_WITH_UNIT.match(text.strip())
    if not match:
        return None, None
    number = match.group("number").replace("−", "-")
    return float(number), match.group("unit")


def parse_markdown_table(
    text: str,
    *,
    table_id: UUID,
    reference: TableEvidenceReference,
    id_prefix: str,
    caption: Optional[str] = None,
) -> TableStructure:
    if "<table" in text.lower():
        raise ExtractionUnsupportedError(EXTRACTION_PROVIDER, "HTML tables with spans are not supported by this converter")
    lines = [line for line in text.strip().splitlines() if line.strip()]
    if len(lines) < 2 or not _is_separator_row(lines[1]):
        raise MalformedProviderResponseError(EXTRACTION_PROVIDER, "no Markdown table header/separator", field="table")

    header_texts = _split_row(lines[0])
    column_count = len(header_texts)
    body = [_split_row(line) for line in lines[2:]]
    for row in body:
        if len(row) != column_count:
            raise MalformedProviderResponseError(
                EXTRACTION_PROVIDER, "a table row has a different number of cells than the header", field="table"
            )

    headers: List[TableHeader] = []
    for column, raw in enumerate(header_texts):
        if not raw:
            continue
        header_text, unit = split_unit(raw)
        headers.append(
            TableHeader(
                header_id=f"{id_prefix}.h{column}",
                orientation=HeaderOrientation.COLUMN,
                text=header_text,
                unit=unit,
                index=column,
            )
        )

    cells: List[TableCell] = []
    for ordinal, row in enumerate(body):
        row_index = ordinal + 1
        for column, raw in enumerate(row):
            if not raw:
                continue
            number, unit = parse_number(raw)
            cells.append(
                TableCell(
                    cell_id=f"{id_prefix}.r{row_index}.c{column}",
                    row_index=row_index,
                    column_index=column,
                    text=raw if unit is None else raw[: raw.rfind(unit)].strip(),
                    numeric_value=number,
                    unit=unit,
                )
            )

    return TableStructure(
        table_id=table_id,
        reference=reference,
        caption=caption,
        row_count=len(body) + 1,
        column_count=column_count,
        headers=headers,
        cells=cells,
    )
