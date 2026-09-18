"""The AgentSpec acceptance fixture, as synthetic test data.

SYNTHETIC MATERIAL. Nothing here was produced by reading a real PDF or
a real lecture, and no provider was called to build it. It is the
hand-worked case the AgentSpec records (docs/architecture/Netra-SPEC.md,
section 4) transcribed into Python:

    A team-authored PDF, "Ohm's Law Study Pack" (ohm-v1), with a
    matching permitted 90-second video (lecture-v1). Graph fig02 has
    current on x and voltage on y; table tbl01 contains (1 A, 2 V),
    (2 A, 4 V), (3 A, 6 V); equation eq01 is V = I × R. These values
    define the acceptance fixture.

Two roles are deliberately kept apart in this module:

- the *extraction* builders (extracted_chart, extracted_table,
  extracted_equation) stand in for what a provider produced,
- the *source check* builders (chart_source_check, table_source_check,
  equation_source_check) stand in for what a reviewer read off the
  original media.

They are separate so that a test can corrupt one without touching the
other, which is the only way a validation test can fail honestly. A
fixture that generated both sides from one definition would prove
nothing: extraction would be checked against itself.

What this fixture cannot establish, and no test in this repository
currently does: that a real extraction provider reads a real figure
correctly. That needs the original media and a person looking at it
(current-scope.md: "a text judge alone cannot verify visual fidelity").
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from netra_api.multimedia.equations.models import (
    EquationEvidenceReference,
    EquationNode,
    EquationNodeKind,
    EquationTree,
)
from netra_api.multimedia.equations.validation import EquationSourceCheck
from netra_api.multimedia.evidence import ObservationSource
from netra_api.multimedia.figures.charts import (
    AxisOrientation,
    AxisScale,
    ChartAxis,
    ChartKind,
    ChartPoint,
    ChartSeries,
    ChartStructure,
)
from netra_api.multimedia.figures.evidence import FigureEvidenceReference
from netra_api.multimedia.figures.validation import (
    AxisSourceCheck,
    ChartSourceCheck,
    SeriesPointSourceCheck,
    SeriesSourceCheck,
)
from netra_api.multimedia.tables.models import (
    HeaderOrientation,
    TableCell,
    TableEvidenceReference,
    TableHeader,
    TableStructure,
)
from netra_api.multimedia.tables.validation import (
    CellSourceCheck,
    HeaderSourceCheck,
    TableSourceCheck,
)
from netra_api.multimedia.video.models import (
    EvidenceProvenance,
    VideoAsset,
    VideoEvidenceItem,
    VideoEvidenceKind,
    VideoEvidenceReference,
    VideoSourceKind,
)

SOURCE_ID = UUID("00000000-0000-4000-8000-00000000ff01")
SOURCE_VERSION_ID = UUID("00000000-0000-4000-8000-00000000ff02")
CHART_ID = UUID("00000000-0000-4000-8000-00000000ff03")
TABLE_ID = UUID("00000000-0000-4000-8000-00000000ff04")
EQUATION_ID = UUID("00000000-0000-4000-8000-00000000ff05")
VIDEO_ID = UUID("00000000-0000-4000-8000-00000000ff06")

FIGURE_INDEX = 2
"""fig02 in the AgentSpec's naming."""
TABLE_INDEX = 1
"""tbl01."""
EQUATION_INDEX = 1
"""eq01."""

VIDEO_LOCATOR = "lecture-v1"
LECTURE_DURATION_MS = 90_000
"""The AgentSpec's "matching permitted 90-second video"."""
QUESTION_TIME_MS = 48_000
"""00:48 — where the lecturer points and says "The resistance stays constant"."""

MEASUREMENTS = ((1.0, 2.0), (2.0, 4.0), (3.0, 6.0))
"""(current in A, voltage in V), exactly as the AgentSpec records them."""

PRODUCED_AT = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)


def figure_reference() -> FigureEvidenceReference:
    return FigureEvidenceReference(
        evidence_id="ev-fig02",
        source_version_id=SOURCE_VERSION_ID,
        locator="p4/fig02",
        figure_index=FIGURE_INDEX,
    )


def extracted_chart(
    *,
    unreadable_axes: bool = False,
    y_unit: str = "V",
    swap_axes: bool = False,
) -> ChartStructure:
    """fig02 as extraction produced it.

    unreadable_axes reproduces the AgentSpec's "repeat with an
    unreadable variant". y_unit and swap_axes let a test inject the
    wrong-unit and wrong-axis failures the acceptance criteria call out
    without hand-building a whole chart.
    """

    if unreadable_axes:
        x_axis = ChartAxis(
            axis_id="fig02.x",
            orientation=AxisOrientation.X,
            label=None,
            label_source=ObservationSource.UNREADABLE,
            unit=None,
            unit_source=ObservationSource.UNREADABLE,
            scale=AxisScale.UNKNOWN,
        )
        y_axis = ChartAxis(
            axis_id="fig02.y",
            orientation=AxisOrientation.Y,
            label=None,
            label_source=ObservationSource.UNREADABLE,
            unit=None,
            unit_source=ObservationSource.UNREADABLE,
            scale=AxisScale.UNKNOWN,
        )
    else:
        x_label, y_label = ("voltage", "current") if swap_axes else ("current", "voltage")
        x_unit, resolved_y_unit = ("V", "A") if swap_axes else ("A", y_unit)
        x_axis = ChartAxis(
            axis_id="fig02.x",
            orientation=AxisOrientation.X,
            label=x_label,
            unit=x_unit,
            scale=AxisScale.LINEAR,
            minimum=0.0,
            maximum=4.0,
        )
        y_axis = ChartAxis(
            axis_id="fig02.y",
            orientation=AxisOrientation.Y,
            label=y_label,
            unit=resolved_y_unit,
            scale=AxisScale.LINEAR,
            minimum=0.0,
            maximum=8.0,
        )

    points = []
    for ordinal, (current, voltage) in enumerate(MEASUREMENTS):
        point_id = f"fig02.s1.p{ordinal}"
        if unreadable_axes:
            # With no legible axes there is nothing to read a coordinate
            # against, so the plotted values are unreadable too. A point
            # marked unreadable must carry no coordinates.
            points.append(
                ChartPoint(point_id=point_id, value_source=ObservationSource.UNREADABLE)
            )
        else:
            points.append(
                ChartPoint(
                    point_id=point_id,
                    x=current,
                    y=voltage,
                    value_source=ObservationSource.OBSERVED,
                )
            )

    return ChartStructure(
        chart_id=CHART_ID,
        reference=figure_reference(),
        kind=ChartKind.LINE,
        title="Voltage against current for one resistor",
        axes=[x_axis, y_axis],
        series=[
            ChartSeries(series_id="fig02.s1", label="measured resistor", points=points)
        ],
    )


def chart_source_check(*, unreadable_axes: bool = False) -> ChartSourceCheck:
    """fig02 as a reviewer read it off the original PDF."""

    return ChartSourceCheck(
        figure_index=FIGURE_INDEX,
        axes=[
            AxisSourceCheck(
                orientation=AxisOrientation.X,
                label=None if unreadable_axes else "current",
                unit=None if unreadable_axes else "A",
                unreadable_in_source=unreadable_axes,
            ),
            AxisSourceCheck(
                orientation=AxisOrientation.Y,
                label=None if unreadable_axes else "voltage",
                unit=None if unreadable_axes else "V",
                unreadable_in_source=unreadable_axes,
            ),
        ],
        series=[
            SeriesSourceCheck(
                series_id="fig02.s1",
                label="measured resistor",
                # The AgentSpec states these plotted values exactly, and
                # tbl01 prints the same pairs, so the reviewer is not
                # estimating off gridlines here: compare them strictly.
                unreadable_in_source=unreadable_axes,
                points=[
                    SeriesPointSourceCheck(x=current, y=voltage)
                    for current, voltage in MEASUREMENTS
                ],
            )
        ],
    )


def table_reference() -> TableEvidenceReference:
    return TableEvidenceReference(
        evidence_id="ev-tbl01",
        source_version_id=SOURCE_VERSION_ID,
        locator="p4/tbl01",
        table_index=TABLE_INDEX,
    )


def extracted_table(*, voltage_unit: str = "V", drop_row: int | None = None) -> TableStructure:
    """tbl01 as extraction produced it: two columns, three measurement rows.

    Row 0 holds the headers, rows 1-3 the measurements, so the grid
    matches what a reader encounters rather than a headerless array.
    """

    headers = [
        TableHeader(
            header_id="tbl01.h0",
            orientation=HeaderOrientation.COLUMN,
            text="Current",
            unit="A",
            index=0,
        ),
        TableHeader(
            header_id="tbl01.h1",
            orientation=HeaderOrientation.COLUMN,
            text="Voltage",
            unit=voltage_unit,
            index=1,
        ),
    ]
    cells = []
    for ordinal, (current, voltage) in enumerate(MEASUREMENTS):
        row_index = ordinal + 1
        if drop_row is not None and row_index == drop_row:
            continue
        cells.append(
            TableCell(
                cell_id=f"tbl01.r{row_index}.c0",
                row_index=row_index,
                column_index=0,
                text=f"{int(current)}",
                numeric_value=current,
            )
        )
        cells.append(
            TableCell(
                cell_id=f"tbl01.r{row_index}.c1",
                row_index=row_index,
                column_index=1,
                text=f"{int(voltage)}",
                numeric_value=voltage,
            )
        )

    return TableStructure(
        table_id=TABLE_ID,
        reference=table_reference(),
        caption="Measured current and voltage",
        row_count=4,
        column_count=2,
        headers=headers,
        cells=cells,
    )


def table_source_check() -> TableSourceCheck:
    """tbl01 as a reviewer read it off the original PDF."""

    cells = []
    for ordinal, (current, voltage) in enumerate(MEASUREMENTS):
        row_index = ordinal + 1
        cells.append(
            CellSourceCheck(
                row_index=row_index,
                column_index=0,
                text=f"{int(current)}",
                numeric_value=current,
            )
        )
        cells.append(
            CellSourceCheck(
                row_index=row_index,
                column_index=1,
                text=f"{int(voltage)}",
                numeric_value=voltage,
            )
        )
    return TableSourceCheck(
        table_index=TABLE_INDEX,
        row_count=4,
        column_count=2,
        headers=[
            HeaderSourceCheck(
                orientation=HeaderOrientation.COLUMN, index=0, text="Current", unit="A"
            ),
            HeaderSourceCheck(
                orientation=HeaderOrientation.COLUMN, index=1, text="Voltage", unit="V"
            ),
        ],
        cells=cells,
    )


def equation_reference() -> EquationEvidenceReference:
    return EquationEvidenceReference(
        evidence_id="ev-eq01",
        source_version_id=SOURCE_VERSION_ID,
        locator="p4/eq01",
        equation_index=EQUATION_INDEX,
    )


def extracted_equation(*, operator: str = "×", spoken_operator: str = "times") -> EquationTree:
    """eq01 as extraction produced it: V = I × R.

    operator is parameterised so a test can inject the classic silent
    failure — a multiplication read as a division — without rebuilding
    the tree.
    """

    return EquationTree(
        equation_id=EQUATION_ID,
        reference=equation_reference(),
        root=EquationNode(
            node_id="eq01.root",
            kind=EquationNodeKind.OPERATOR,
            symbol="=",
            spoken_form="V equals I times R",
            children=[
                EquationNode(
                    node_id="eq01.v",
                    kind=EquationNodeKind.OPERAND,
                    symbol="V",
                    unit="V",
                    spoken_form="V",
                ),
                EquationNode(
                    node_id="eq01.product",
                    kind=EquationNodeKind.OPERATOR,
                    symbol=operator,
                    spoken_form=f"I {spoken_operator} R",
                    children=[
                        EquationNode(
                            node_id="eq01.i",
                            kind=EquationNodeKind.OPERAND,
                            symbol="I",
                            unit="A",
                            spoken_form="I",
                        ),
                        EquationNode(
                            node_id="eq01.r",
                            kind=EquationNodeKind.OPERAND,
                            symbol="R",
                            unit="ohm",
                            spoken_form="R",
                        ),
                    ],
                ),
            ],
        ),
    )


def equation_source_check() -> EquationSourceCheck:
    """eq01 as a reviewer read it off the original PDF."""

    return EquationSourceCheck(
        equation_index=EQUATION_INDEX,
        canonical_form="(V = (I × R))",
        units_by_symbol={"V": "V", "I": "A", "R": "ohm"},
    )


def lecture_asset() -> VideoAsset:
    return VideoAsset(
        video_id=VIDEO_ID,
        source_id=SOURCE_ID,
        source_version_id=SOURCE_VERSION_ID,
        kind=VideoSourceKind.UPLOAD,
        external_ref="uploads/lecture-v1.mp4",
        duration_ms=LECTURE_DURATION_MS,
    )


def _provenance(stage: str) -> EvidenceProvenance:
    return EvidenceProvenance(
        provider="twelvelabs",
        model_name="pegasus",
        model_version="test-fixture",
        produced_at=PRODUCED_AT,
        stage=stage,
    )


def transcript_evidence() -> VideoEvidenceItem:
    """The AgentSpec's insufficient result: words, no axes.

    "The transcript says 'this line' but omits its axes" — step 3 of the
    walkthrough. This item is what M1 must recognise as a gap.
    """

    return VideoEvidenceItem(
        video_evidence_id=UUID("00000000-0000-4000-8000-00000000ff10"),
        reference=VideoEvidenceReference(
            evidence_id="ev-lec-transcript-48",
            source_version_id=SOURCE_VERSION_ID,
            locator=VIDEO_LOCATOR,
            start_ms=42_000,
            end_ms=58_000,
            video_id=VIDEO_ID,
        ),
        kind=VideoEvidenceKind.TRANSCRIPT_SEGMENT,
        description="Look at this line. The resistance stays constant.",
        provenance=_provenance("derive_video_evidence"),
    )


def visual_evidence() -> VideoEvidenceItem:
    """The evidence step 4 retrieves: what is actually on screen at 00:48."""

    return VideoEvidenceItem(
        video_evidence_id=UUID("00000000-0000-4000-8000-00000000ff11"),
        reference=VideoEvidenceReference(
            evidence_id="ev-lec-visual-48",
            source_version_id=SOURCE_VERSION_ID,
            locator=VIDEO_LOCATOR,
            start_ms=44_000,
            end_ms=52_000,
            video_id=VIDEO_ID,
        ),
        kind=VideoEvidenceKind.VISUAL_DESCRIPTION,
        description=(
            "A slide shows a straight line through the origin on axes labelled "
            "current in amperes and voltage in volts."
        ),
        provenance=_provenance("derive_video_evidence"),
    )
