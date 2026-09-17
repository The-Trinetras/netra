"""Validate an extracted ChartStructure against the original figure.

multimedia.md: "Preserve chart axes, scales, series and exact values
where actually available" and "Use source-checked fixtures for labels,
relationships, signs, units and timestamps." The AgentSpec's first
acceptance question is "compare the graph axes, three table rows and
equation in section 4 against the originals ... Wrong units, invented
values or silently lost grouping fail the check."

ChartSourceCheck is the reviewer's record of what the original figure
actually shows. It is authored by a person looking at the media, or by
a fixture derived from one; it is never produced by the same extraction
it checks, which would make the check circular.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from netra_api.multimedia.evidence import ObservationSource
from netra_api.multimedia.figures.charts import AxisOrientation, ChartStructure
from netra_api.multimedia.validation import (
    SourceCheckStatus,
    ValidationFinding,
    ValidationReport,
    finding,
    normalize_number,
)


class AxisSourceCheck(BaseModel):
    """What the original figure shows for one axis.

    unreadable_in_source marks an axis a reviewer looked at and could
    not read. That is the AgentSpec's "repeat with an unreadable
    variant" case: the run must end with a stated limitation, so the
    expected values below become a recorded gap rather than a target
    extraction could be scored against.
    """

    orientation: AxisOrientation
    label: Optional[str] = None
    unit: Optional[str] = None
    unreadable_in_source: bool = False


class SeriesPointSourceCheck(BaseModel):
    """One plotted value a reviewer read off the original figure."""

    x: float
    y: float


class SeriesSourceCheck(BaseModel):
    series_id: str
    label: Optional[str] = None
    points: List[SeriesPointSourceCheck] = Field(default_factory=list)
    unreadable_in_source: bool = True
    """Whether the reviewer could read the plotted values off the figure.

    Defaults to True — "we could not read these" — because a reviewer
    recording exact coordinates from a plot is doing something unusual.
    Values read off gridlines are estimates, and scoring extraction
    against an estimate as if it were exact would manufacture mismatches
    out of the reviewer's own uncertainty. A record that genuinely
    carries exact values, from printed data labels or the figure's own
    source data, sets this to False and is then compared strictly."""


class ChartSourceCheck(BaseModel):
    """A reviewer's record of the original figure, used to check extraction."""

    figure_index: int = Field(ge=0)
    axes: List[AxisSourceCheck] = Field(default_factory=list)
    series: List[SeriesSourceCheck] = Field(default_factory=list)


def validate_chart_against_source(
    chart: ChartStructure, expectation: ChartSourceCheck
) -> ValidationReport:
    """Compare chart field by field against what a reviewer read off the figure.

    Reports, per axis: the quantity label and the unit. Per series: the
    label, the number of plotted points, and each point's coordinates.

    A missing axis or series is a MISMATCH with no extracted value, not
    a silent omission — "the graph has no y axis" is exactly the kind of
    dropped detail that would otherwise pass unnoticed.
    """

    report = ValidationReport(object_id=str(chart.chart_id))
    report.findings.extend(_axis_findings(chart, expectation))
    report.findings.extend(_series_findings(chart, expectation))
    if chart.reference.figure_index != expectation.figure_index:
        report.findings.append(
            finding(
                part_id=str(chart.chart_id),
                field="figure_index",
                expected=expectation.figure_index,
                extracted=chart.reference.figure_index,
                detail="extraction was checked against a different figure",
            )
        )
    return report


def _axis_findings(
    chart: ChartStructure, expectation: ChartSourceCheck
) -> List[ValidationFinding]:
    findings: List[ValidationFinding] = []
    for expected_axis in expectation.axes:
        part_id = f"axis:{expected_axis.orientation.value}"
        extracted_axis = chart.axis(expected_axis.orientation)
        if extracted_axis is None:
            findings.append(
                finding(
                    part_id=part_id,
                    field="presence",
                    expected="present",
                    extracted=None,
                    detail="the figure has this axis but extraction produced none",
                )
            )
            continue
        part_id = extracted_axis.axis_id
        unreadable = expected_axis.unreadable_in_source
        findings.append(
            finding(
                part_id=part_id,
                field="label",
                expected=expected_axis.label,
                extracted=extracted_axis.label,
                unreadable_in_source=unreadable,
                detail=(
                    "axis label is illegible in the original figure"
                    if unreadable
                    else None
                ),
            )
        )
        findings.append(
            finding(
                part_id=part_id,
                field="unit",
                expected=expected_axis.unit,
                extracted=extracted_axis.unit,
                unreadable_in_source=unreadable,
            )
        )
        if not unreadable and extracted_axis.label_source is not ObservationSource.OBSERVED:
            findings.append(
                ValidationFinding(
                    part_id=part_id,
                    field="label_source",
                    status=SourceCheckStatus.MISMATCH,
                    expected=ObservationSource.OBSERVED.value,
                    extracted=extracted_axis.label_source.value,
                    detail=(
                        "the axis label is legible in the original figure, so a "
                        "reconstructed label is a downgrade the student would not "
                        "be told about"
                    ),
                )
            )
    return findings


def _series_findings(
    chart: ChartStructure, expectation: ChartSourceCheck
) -> List[ValidationFinding]:
    findings: List[ValidationFinding] = []
    extracted_by_id = {series.series_id: series for series in chart.series}
    for expected_series in expectation.series:
        extracted_series = extracted_by_id.get(expected_series.series_id)
        if extracted_series is None:
            findings.append(
                finding(
                    part_id=expected_series.series_id,
                    field="presence",
                    expected="present",
                    extracted=None,
                    detail="the figure plots this series but extraction produced none",
                )
            )
            continue
        findings.append(
            finding(
                part_id=expected_series.series_id,
                field="label",
                expected=expected_series.label,
                extracted=extracted_series.label,
            )
        )
        findings.append(
            finding(
                part_id=expected_series.series_id,
                field="point_count",
                expected=len(expected_series.points),
                extracted=len(extracted_series.points),
                detail="a dropped or invented plotted value changes the relationship",
            )
        )
        findings.extend(
            _point_findings(expected_series.series_id, expected_series, extracted_series)
        )
    return findings


def _point_findings(series_id: str, expected_series, extracted_series) -> List[ValidationFinding]:
    findings: List[ValidationFinding] = []
    extracted_points = {
        _point_key(point.x, point.y): point for point in extracted_series.points
    }
    for ordinal, expected_point in enumerate(expected_series.points):
        part_id = f"{series_id}:p{ordinal}"
        key = _point_key(expected_point.x, expected_point.y)
        matched = extracted_points.get(key)
        findings.append(
            finding(
                part_id=part_id,
                field="coordinates",
                expected=key,
                extracted=key if matched is not None else _nearest_extracted(
                    extracted_series, expected_point
                ),
                unreadable_in_source=expected_series.unreadable_in_source,
                detail=(
                    "the reviewer did not read exact coordinates off this figure"
                    if expected_series.unreadable_in_source
                    else None
                ),
            )
        )
    return findings


def _nearest_extracted(extracted_series, expected_point) -> Optional[str]:
    """Render the extracted point closest in x, to make a mismatch legible.

    Only used to fill a finding's "extracted" side when no exact match
    exists. It is a reporting aid: nothing treats proximity as a match,
    because a value read one gridline off is wrong, not nearly right.
    """

    candidates = [point for point in extracted_series.points if point.x is not None]
    if not candidates:
        return None
    nearest = min(candidates, key=lambda point: abs(point.x - expected_point.x))
    return _point_key(nearest.x, nearest.y)


def _point_key(x: Optional[float], y: Optional[float]) -> str:
    return f"({normalize_number(x)}, {normalize_number(y)})"


def chart_check_index(checks: List[ChartSourceCheck]) -> Dict[int, ChartSourceCheck]:
    """Index reviewer records by figure_index for lookup during a job run."""

    return {check.figure_index: check for check in checks}
