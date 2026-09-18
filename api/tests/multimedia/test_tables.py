"""Table structure navigation and validation against the original table.

The AgentSpec's student asks "where does the table show the same
thing?", which is a question about cell relationships. These tests check
that the grid stays addressable through spans, that headers keep
governing their cells, and that the failures the acceptance criteria
name — wrong units, invented values, lost grouping — are actually
caught.
"""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from fixtures.ohm_law import (
    extracted_table,
    table_reference,
    table_source_check,
)
from netra_api.multimedia.evidence import ObservationSource
from netra_api.multimedia.tables.models import (
    HeaderOrientation,
    TableCell,
    TableHeader,
    TableStructure,
)
from netra_api.multimedia.tables.service import citable_tables, first_unverified_reason
from netra_api.multimedia.tables.validation import (
    extra_extracted_cells,
    resolve_cell_unit,
    validate_table_against_source,
)
from netra_api.multimedia.validation import ValidationReport


def _spanned_table() -> TableStructure:
    """A 2x3 grid whose first cell spans both columns of its row."""

    return TableStructure(
        table_id=uuid4(),
        reference=table_reference(),
        row_count=2,
        column_count=3,
        headers=[
            TableHeader(
                header_id="h.measurements",
                orientation=HeaderOrientation.COLUMN,
                text="Measurements",
                index=0,
                span=2,
            ),
            TableHeader(
                header_id="h.notes",
                orientation=HeaderOrientation.COLUMN,
                text="Notes",
                index=2,
            ),
        ],
        cells=[
            TableCell(
                cell_id="c.wide",
                row_index=1,
                column_index=0,
                column_span=2,
                text="1 A at 2 V",
            ),
            TableCell(cell_id="c.note", row_index=1, column_index=2, text="baseline"),
        ],
    )


def test_fixture_table_holds_the_agentspec_rows():
    table = extracted_table()

    rows = [
        (
            table.cell_at(row, 0).numeric_value,
            table.cell_at(row, 1).numeric_value,
        )
        for row in range(1, 4)
    ]
    assert rows == [(1.0, 2.0), (2.0, 4.0), (3.0, 6.0)]


def test_find_row_by_value_answers_where_the_table_shows_two_amperes():
    table = extracted_table()

    row_index = table.find_row_by_value(column_index=0, numeric_value=2.0)

    assert row_index == 2
    assert table.cell_at(row_index, 1).numeric_value == 4.0


def test_find_row_by_value_returns_none_for_a_value_not_in_the_table():
    assert extracted_table().find_row_by_value(column_index=0, numeric_value=9.0) is None


def test_headers_for_names_the_quantity_a_bare_cell_does_not():
    table = extracted_table()

    cell = table.cell_at(1, 1)
    headers = table.headers_for(cell)

    assert [header.text for header in headers] == ["Voltage"]


def test_a_merged_cell_answers_for_every_position_it_covers():
    """Navigating right across a span must not report an empty position."""

    table = _spanned_table()

    assert table.cell_at(1, 0).cell_id == "c.wide"
    assert table.cell_at(1, 1).cell_id == "c.wide"
    assert table.cell_at(1, 2).cell_id == "c.note"


def test_a_spanned_header_governs_every_column_in_its_band():
    table = _spanned_table()

    wide = table.cell_at(1, 1)

    assert [header.header_id for header in table.headers_for(wide)] == ["h.measurements"]


def test_row_returns_every_grid_position_with_spans_resolved():
    table = _spanned_table()

    assert [cell.cell_id for cell in table.row(1)] == ["c.wide", "c.wide", "c.note"]


def test_overlapping_cells_are_rejected():
    with pytest.raises(ValidationError) as excinfo:
        TableStructure(
            table_id=uuid4(),
            reference=table_reference(),
            row_count=1,
            column_count=2,
            cells=[
                TableCell(cell_id="a", row_index=0, column_index=0, column_span=2, text="a"),
                TableCell(cell_id="b", row_index=0, column_index=1, text="b"),
            ],
        )

    assert "overlaps" in str(excinfo.value)


def test_a_cell_extending_past_the_declared_grid_is_rejected():
    with pytest.raises(ValidationError):
        TableStructure(
            table_id=uuid4(),
            reference=table_reference(),
            row_count=1,
            column_count=1,
            cells=[
                TableCell(cell_id="a", row_index=0, column_index=0, column_span=3, text="a")
            ],
        )


def test_an_unreadable_cell_must_not_carry_text():
    with pytest.raises(ValidationError):
        TableCell(
            cell_id="a",
            row_index=0,
            column_index=0,
            text="2",
            text_source=ObservationSource.UNREADABLE,
        )


def test_correct_extraction_is_source_verified():
    report = validate_table_against_source(extracted_table(), table_source_check())

    assert report.mismatches == []
    assert report.is_source_verified is True


def test_a_wrong_header_unit_fails_the_check():
    """Millivolts in the header silently rescales every cell below it."""

    report = validate_table_against_source(
        extracted_table(voltage_unit="mV"), table_source_check()
    )

    assert report.is_source_verified is False
    unit_mismatch = next(f for f in report.mismatches if f.field == "unit")
    assert (unit_mismatch.expected, unit_mismatch.extracted) == ("V", "mV")


def test_a_dropped_row_is_reported_as_missing_cells():
    report = validate_table_against_source(
        extracted_table(drop_row=2), table_source_check()
    )

    assert report.is_source_verified is False
    missing = [f for f in report.mismatches if f.field == "presence"]
    assert {f.part_id for f in missing} == {"cell:2:0", "cell:2:1"}


def test_a_wrong_cell_value_is_reported():
    table = extracted_table()
    corrupted = table.model_copy(
        update={
            "cells": [
                cell.model_copy(update={"numeric_value": 99.0, "text": "99"})
                if cell.cell_id == "tbl01.r2.c1"
                else cell
                for cell in table.cells
            ]
        }
    )

    report = validate_table_against_source(corrupted, table_source_check())

    assert report.is_source_verified is False
    assert any(
        f.part_id == "tbl01.r2.c1" and f.field == "numeric_value"
        for f in report.mismatches
    )


def test_a_lost_header_span_is_reported():
    table = _spanned_table()
    flattened = table.model_copy(
        update={
            "headers": [
                header.model_copy(update={"span": 1}) if header.span > 1 else header
                for header in table.headers
            ]
        }
    )
    expectation = table_source_check().model_copy(
        update={
            "headers": [
                type(table_source_check().headers[0])(
                    orientation=HeaderOrientation.COLUMN,
                    index=0,
                    text="Measurements",
                    span=2,
                )
            ],
            "cells": [],
            "row_count": 2,
            "column_count": 3,
        }
    )

    report = validate_table_against_source(flattened, expectation)

    assert any(f.field == "span" for f in report.mismatches)


def test_an_invented_cell_is_reported_separately():
    """An extra value has no expectation to contradict, so it is listed apart."""

    table = extracted_table()
    with_extra = table.model_copy(
        update={
            "cells": table.cells
            + [
                TableCell(
                    cell_id="tbl01.r0.c0",
                    row_index=0,
                    column_index=0,
                    text="invented",
                )
            ]
        }
    )

    assert extra_extracted_cells(with_extra, table_source_check()) == ["tbl01.r0.c0"]


def test_resolve_cell_unit_falls_back_to_the_governing_header():
    assert resolve_cell_unit(extracted_table(), 1, 1) == "V"
    assert resolve_cell_unit(extracted_table(), 1, 0) == "A"


def test_resolve_cell_unit_prefers_the_cells_own_unit():
    table = extracted_table()
    with_own_unit = table.model_copy(
        update={
            "cells": [
                cell.model_copy(update={"unit": "mV"})
                if cell.cell_id == "tbl01.r1.c1"
                else cell
                for cell in table.cells
            ]
        }
    )

    assert resolve_cell_unit(with_own_unit, 1, 1) == "mV"


def test_citable_tables_excludes_a_table_with_no_report():
    table = extracted_table()

    assert citable_tables([table], {}) == []


def test_citable_tables_includes_a_verified_table():
    table = extracted_table()
    report = validate_table_against_source(table, table_source_check())

    assert citable_tables([table], {table.table_id: report}) == [table]


def test_first_unverified_reason_names_the_limitation():
    verified = validate_table_against_source(extracted_table(), table_source_check())
    mismatched = validate_table_against_source(
        extracted_table(voltage_unit="mV"), table_source_check()
    )

    assert first_unverified_reason(verified) is None
    assert first_unverified_reason(mismatched) == (
        "the extracted table does not match the document"
    )
    assert first_unverified_reason(None) == (
        "this table has not been checked against the document"
    )


def test_an_unchecked_report_is_not_citable():
    empty = ValidationReport(object_id="tbl01")

    assert empty.is_source_verified is False
