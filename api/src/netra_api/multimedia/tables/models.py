"""Table structure — headers, spans, cells and their source mapping.

multimedia.md: "Represent table cells with their headers, spans and
source mapping. Preserve exact values and structure separately from a
summary. Do not flatten a table into prose when the task needs cell
relationships."

That last rule is the whole point of this module. The AgentSpec's
student asks "where does the table show the same thing?" — answering it
means addressing the row where current is 2 A and reading the voltage
beside it, which a paragraph summarising the table cannot do. So a
TableStructure keeps the grid addressable: every cell knows its span,
and every cell can name the headers that govern it.

M3 owns table extraction *semantics* and validation; M2 owns storing
the result and its source/version identity (multimedia.md: "M3
validates table extraction; M2 owns source/table storage and version
identity"). This module therefore defines the shape M3 hands to M2 and
never writes anything itself.
"""

from __future__ import annotations

from enum import Enum
from typing import Dict, List, Optional, Tuple
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from netra_api.multimedia.evidence import ObservationSource, VisualEvidenceReference
from netra_api.multimedia.observations import check_observation


class TableEvidenceReference(VisualEvidenceReference):
    """Anchors a TableStructure to its source table's locator."""

    table_index: int = Field(ge=0)
    """Ordinal of this table within source_version_id, for stable re-reference."""


class HeaderOrientation(str, Enum):
    COLUMN = "column"
    """Governs a column: the header sits above the cells it describes."""
    ROW = "row"
    """Governs a row: the header sits to the side of the cells it describes."""


class TableHeader(BaseModel):
    """One header cell and the band of rows/columns it governs."""

    header_id: str
    orientation: HeaderOrientation
    text: Optional[str] = None
    text_source: ObservationSource = ObservationSource.OBSERVED
    unit: Optional[str] = None
    unit_source: ObservationSource = ObservationSource.OBSERVED
    """Units often live in the header rather than in each cell ("Current
    (A)"). Keeping the unit addressable is what lets validation catch a
    table read in the right shape but the wrong units."""
    index: int = Field(ge=0)
    """First column (COLUMN orientation) or row (ROW) this header governs."""
    span: int = Field(default=1, ge=1)
    """How many columns/rows it governs, for merged header cells."""

    @model_validator(mode="after")
    def _check_observation_invariants(self) -> "TableHeader":
        check_observation(f"{self.header_id}.text", self.text_source, self.text)
        check_observation(f"{self.header_id}.unit", self.unit_source, self.unit)
        return self

    def governs(self, row_index: int, column_index: int) -> bool:
        position = column_index if self.orientation is HeaderOrientation.COLUMN else row_index
        return self.index <= position < self.index + self.span


class TableCell(BaseModel):
    """One data cell, addressed by its top-left position plus its span."""

    cell_id: str
    row_index: int = Field(ge=0)
    column_index: int = Field(ge=0)
    row_span: int = Field(default=1, ge=1)
    column_span: int = Field(default=1, ge=1)
    text: Optional[str] = None
    text_source: ObservationSource = ObservationSource.OBSERVED
    numeric_value: Optional[float] = None
    """Parsed numeric value when the cell holds one, kept beside the text
    rather than replacing it. "2" and "2 V" must stay distinguishable:
    the text is what the source says, the number is what arithmetic may
    use."""
    unit: Optional[str] = None
    """Unit carried by the cell itself, when it is not implied by a header."""

    @model_validator(mode="after")
    def _check_observation_invariants(self) -> "TableCell":
        check_observation(f"{self.cell_id}.text", self.text_source, self.text)
        return self

    def covers(self, row_index: int, column_index: int) -> bool:
        return (
            self.row_index <= row_index < self.row_index + self.row_span
            and self.column_index <= column_index < self.column_index + self.column_span
        )


class TableStructure(BaseModel):
    """One extracted table: its grid, its headers and its source anchor."""

    table_id: UUID
    reference: TableEvidenceReference
    caption: Optional[str] = None
    caption_source: ObservationSource = ObservationSource.OBSERVED
    row_count: int = Field(ge=0)
    column_count: int = Field(ge=0)
    headers: List[TableHeader] = Field(default_factory=list)
    cells: List[TableCell] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_structure(self) -> "TableStructure":
        check_observation("caption", self.caption_source, self.caption)

        header_ids = [header.header_id for header in self.headers]
        if len(set(header_ids)) != len(header_ids):
            raise ValueError("header_id values must be unique within a table")
        cell_ids = [cell.cell_id for cell in self.cells]
        if len(set(cell_ids)) != len(cell_ids):
            raise ValueError("cell_id values must be unique within a table")

        occupied: Dict[Tuple[int, int], str] = {}
        for cell in self.cells:
            if cell.row_index + cell.row_span > self.row_count:
                raise ValueError(f"{cell.cell_id} extends past row_count")
            if cell.column_index + cell.column_span > self.column_count:
                raise ValueError(f"{cell.cell_id} extends past column_count")
            for row in range(cell.row_index, cell.row_index + cell.row_span):
                for column in range(
                    cell.column_index, cell.column_index + cell.column_span
                ):
                    previous = occupied.get((row, column))
                    if previous is not None:
                        raise ValueError(
                            f"{cell.cell_id} overlaps {previous} at ({row}, {column})"
                        )
                    occupied[(row, column)] = cell.cell_id
        return self

    def cell_at(self, row_index: int, column_index: int) -> Optional[TableCell]:
        """The cell occupying a grid position, resolving spans.

        A merged cell answers for every position it covers, not only its
        top-left corner. Without that, navigating right across a spanned
        cell reports an empty position and the student is told a value is
        missing when it is merely wide.
        """

        for cell in self.cells:
            if cell.covers(row_index, column_index):
                return cell
        return None

    def headers_for(self, cell: TableCell) -> List[TableHeader]:
        """Every header governing cell, column headers first.

        This is the relationship a flattened summary destroys: "2" means
        nothing, "Current (A) = 2" is the answer to the student's
        question.
        """

        return [
            header
            for header in sorted(self.headers, key=lambda h: (h.orientation.value, h.index))
            if header.governs(cell.row_index, cell.column_index)
        ]

    def row(self, row_index: int) -> List[Optional[TableCell]]:
        """Every grid position in one row, left to right, spans resolved."""

        return [self.cell_at(row_index, column) for column in range(self.column_count)]

    def find_row_by_value(
        self, column_index: int, numeric_value: float
    ) -> Optional[int]:
        """Index of the first row whose cell in column_index holds numeric_value.

        Deterministic lookup, not interpretation: this is how "where does
        the table show 2 amperes" is answered without a model call
        (multimedia.md: "Navigation through an established structure is
        deterministic application logic").
        """

        for row_index in range(self.row_count):
            cell = self.cell_at(row_index, column_index)
            if cell is not None and cell.numeric_value == numeric_value:
                return row_index
        return None
