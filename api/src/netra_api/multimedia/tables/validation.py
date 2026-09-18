"""Validate an extracted TableStructure against the original table.

The AgentSpec's acceptance fixture is a three-row measurement table,
and its failure list is explicit: "Wrong units, invented values or
silently lost grouping fail the check." All three are checked here —
units from the governing header, values cell by cell, and grouping from
the header spans that make a merged header still govern the right band.

As in netra_api.multimedia.figures.validation, the expectation is a
reviewer's record of the original media. Extraction never authors the
record it is checked against.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

from netra_api.multimedia.tables.models import (
    HeaderOrientation,
    TableStructure,
)
from netra_api.multimedia.validation import (
    ValidationFinding,
    ValidationReport,
    finding,
)


class HeaderSourceCheck(BaseModel):
    """What the original table shows for one header."""

    orientation: HeaderOrientation
    index: int = Field(ge=0)
    text: Optional[str] = None
    unit: Optional[str] = None
    span: int = Field(default=1, ge=1)
    unreadable_in_source: bool = False


class CellSourceCheck(BaseModel):
    """What the original table shows in one cell."""

    row_index: int = Field(ge=0)
    column_index: int = Field(ge=0)
    text: Optional[str] = None
    numeric_value: Optional[float] = None
    unreadable_in_source: bool = False


class TableSourceCheck(BaseModel):
    """A reviewer's record of the original table, used to check extraction."""

    table_index: int = Field(ge=0)
    row_count: Optional[int] = None
    column_count: Optional[int] = None
    headers: List[HeaderSourceCheck] = Field(default_factory=list)
    cells: List[CellSourceCheck] = Field(default_factory=list)


def validate_table_against_source(
    table: TableStructure, expectation: TableSourceCheck
) -> ValidationReport:
    """Compare table's shape, headers and cell values against the original."""

    report = ValidationReport(object_id=str(table.table_id))

    if table.reference.table_index != expectation.table_index:
        report.findings.append(
            finding(
                part_id=str(table.table_id),
                field="table_index",
                expected=expectation.table_index,
                extracted=table.reference.table_index,
                detail="extraction was checked against a different table",
            )
        )
    report.findings.append(
        finding(
            part_id=str(table.table_id),
            field="row_count",
            expected=expectation.row_count,
            extracted=table.row_count,
        )
    )
    report.findings.append(
        finding(
            part_id=str(table.table_id),
            field="column_count",
            expected=expectation.column_count,
            extracted=table.column_count,
        )
    )
    report.findings.extend(_header_findings(table, expectation))
    report.findings.extend(_cell_findings(table, expectation))
    return report


def _header_findings(
    table: TableStructure, expectation: TableSourceCheck
) -> List[ValidationFinding]:
    findings: List[ValidationFinding] = []
    for expected_header in expectation.headers:
        part_id = f"header:{expected_header.orientation.value}:{expected_header.index}"
        extracted = next(
            (
                header
                for header in table.headers
                if header.orientation is expected_header.orientation
                and header.index == expected_header.index
            ),
            None,
        )
        if extracted is None:
            findings.append(
                finding(
                    part_id=part_id,
                    field="presence",
                    expected="present",
                    extracted=None,
                    detail="the table has this header but extraction produced none",
                )
            )
            continue
        part_id = extracted.header_id
        findings.append(
            finding(
                part_id=part_id,
                field="text",
                expected=expected_header.text,
                extracted=extracted.text,
                unreadable_in_source=expected_header.unreadable_in_source,
            )
        )
        findings.append(
            finding(
                part_id=part_id,
                field="unit",
                expected=expected_header.unit,
                extracted=extracted.unit,
                unreadable_in_source=expected_header.unreadable_in_source,
                detail="a header unit read wrong silently rescales every cell below it",
            )
        )
        findings.append(
            finding(
                part_id=part_id,
                field="span",
                expected=expected_header.span,
                extracted=extracted.span,
                detail="a lost span detaches cells from the header that governs them",
            )
        )
    return findings


def _cell_findings(
    table: TableStructure, expectation: TableSourceCheck
) -> List[ValidationFinding]:
    findings: List[ValidationFinding] = []
    for expected_cell in expectation.cells:
        part_id = f"cell:{expected_cell.row_index}:{expected_cell.column_index}"
        extracted = table.cell_at(expected_cell.row_index, expected_cell.column_index)
        if extracted is None:
            findings.append(
                finding(
                    part_id=part_id,
                    field="presence",
                    expected="present",
                    extracted=None,
                    detail="the table has a value at this position but extraction has none",
                )
            )
            continue
        part_id = extracted.cell_id
        findings.append(
            finding(
                part_id=part_id,
                field="text",
                expected=expected_cell.text,
                extracted=extracted.text,
                unreadable_in_source=expected_cell.unreadable_in_source,
            )
        )
        findings.append(
            finding(
                part_id=part_id,
                field="numeric_value",
                expected=expected_cell.numeric_value,
                extracted=extracted.numeric_value,
                unreadable_in_source=expected_cell.unreadable_in_source,
            )
        )
    return findings


def extra_extracted_cells(
    table: TableStructure, expectation: TableSourceCheck
) -> List[str]:
    """cell_ids extraction produced at positions the reviewer recorded nothing for.

    Reported separately from the findings because an extra cell is not a
    field comparison: it is a value that may have been invented. A caller
    deciding whether extraction is trustworthy needs to see it even
    though no expectation exists to compare it against.
    """

    expected_positions = {
        (cell.row_index, cell.column_index) for cell in expectation.cells
    }
    if not expected_positions:
        return []
    return [
        cell.cell_id
        for cell in table.cells
        if (cell.row_index, cell.column_index) not in expected_positions
    ]


def resolve_cell_unit(table: TableStructure, row_index: int, column_index: int) -> Optional[str]:
    """The unit governing one grid position: the cell's own, else its header's.

    Column headers are consulted before row headers, matching
    TableStructure.headers_for's ordering, because a measurement table
    puts the quantity's unit in the column head.
    """

    cell = table.cell_at(row_index, column_index)
    if cell is None:
        return None
    if cell.unit is not None:
        return cell.unit
    for header in table.headers_for(cell):
        if header.unit is not None:
            return header.unit
    return None
