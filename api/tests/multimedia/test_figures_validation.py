"""Check extracted chart structure against the reviewer's record of fig02.

The AgentSpec's acceptance question: "Compare the graph axes, three
table rows and equation in section 4 against the originals, then repeat
with an unreadable variant. Record each mismatch. Wrong units, invented
values or silently lost grouping fail the check; the unreadable case
must produce a stated limitation."
"""

from fixtures.ohm_law import (
    chart_source_check,
    extracted_chart,
)
from netra_api.multimedia.evidence import ObservationSource
from netra_api.multimedia.figures.charts import AxisOrientation
from netra_api.multimedia.figures.validation import validate_chart_against_source
from netra_api.multimedia.validation import SourceCheckStatus


def test_correct_extraction_is_source_verified():
    report = validate_chart_against_source(extracted_chart(), chart_source_check())

    assert report.mismatches == []
    assert report.is_source_verified is True


def test_wrong_unit_fails_the_check():
    """"Wrong units ... fail the check" — millivolts is not volts."""

    report = validate_chart_against_source(
        extracted_chart(y_unit="mV"), chart_source_check()
    )

    assert report.is_source_verified is False
    unit_mismatch = next(
        f for f in report.mismatches if f.field == "unit" and f.part_id == "fig02.y"
    )
    assert (unit_mismatch.expected, unit_mismatch.extracted) == ("V", "mV")


def test_swapped_axes_fail_the_check():
    """Current on y and voltage on x inverts the relationship being explained."""

    report = validate_chart_against_source(
        extracted_chart(swap_axes=True), chart_source_check()
    )

    assert report.is_source_verified is False
    mismatched_fields = {(f.part_id, f.field) for f in report.mismatches}
    assert ("fig02.x", "label") in mismatched_fields
    assert ("fig02.y", "label") in mismatched_fields


def test_unreadable_axes_produce_a_stated_limitation_not_a_pass():
    """The unreadable variant must not read as either a match or a mismatch."""

    report = validate_chart_against_source(
        extracted_chart(unreadable_axes=True),
        chart_source_check(unreadable_axes=True),
    )

    assert report.is_source_verified is False
    assert report.mismatches == []
    assert {"fig02.x", "fig02.y"} <= {f.part_id for f in report.unreadable}


def test_extraction_that_invents_a_label_for_an_unreadable_axis_is_caught():
    """Extraction claims to have read axes the reviewer could not read.

    The comparison cannot call this a mismatch — there is no source value
    to contradict — so it must still refuse to pass, which is what
    UNREADABLE_IN_SOURCE does.
    """

    report = validate_chart_against_source(
        extracted_chart(), chart_source_check(unreadable_axes=True)
    )

    assert report.is_source_verified is False
    invented = [f for f in report.unreadable if f.field == "label"]
    assert {f.extracted for f in invented} == {"current", "voltage"}


def test_a_generated_axis_label_is_reported_when_the_source_is_legible():
    """A reconstructed label where the source is readable is a downgrade.

    The text may even be right. It was not read, and a student told "the
    x axis is current" deserves that to be an observation.
    """

    chart = extracted_chart()
    x_axis = chart.axis(AxisOrientation.X)
    generated = chart.model_copy(
        update={
            "axes": [
                x_axis.model_copy(update={"label_source": ObservationSource.GENERATED}),
                chart.axis(AxisOrientation.Y),
            ]
        }
    )

    report = validate_chart_against_source(generated, chart_source_check())

    assert report.is_source_verified is False
    assert any(f.field == "label_source" for f in report.mismatches)


def test_a_dropped_plotted_value_is_reported():
    chart = extracted_chart()
    fewer_points = chart.model_copy(
        update={
            "series": [
                chart.series[0].model_copy(
                    update={"points": chart.series[0].points[:2]}
                )
            ]
        }
    )

    report = validate_chart_against_source(fewer_points, chart_source_check())

    assert report.is_source_verified is False
    count_finding = next(f for f in report.findings if f.field == "point_count")
    assert (count_finding.expected, count_finding.extracted) == ("3", "2")


def test_a_missing_axis_is_a_mismatch_not_a_silent_omission():
    chart = extracted_chart()
    only_x = chart.model_copy(update={"axes": [chart.axis(AxisOrientation.X)]})

    report = validate_chart_against_source(only_x, chart_source_check())

    presence = next(
        f for f in report.mismatches if f.part_id == "axis:y" and f.field == "presence"
    )
    assert presence.extracted is None


def test_validation_against_the_wrong_figure_is_reported():
    report = validate_chart_against_source(
        extracted_chart(), chart_source_check().model_copy(update={"figure_index": 7})
    )

    assert any(f.field == "figure_index" for f in report.mismatches)


def test_an_empty_report_is_never_source_verified():
    """Absence of a check is not a pass."""

    report = validate_chart_against_source(
        extracted_chart(), chart_source_check().model_copy(update={"axes": [], "series": []})
    )

    assert report.checked_findings == []
    assert report.is_source_verified is False


def test_summary_counts_each_status():
    report = validate_chart_against_source(
        extracted_chart(y_unit="mV"), chart_source_check()
    )

    assert "mismatched" in report.summary()
    assert all(
        f.status is not SourceCheckStatus.UNSUPPORTED for f in report.findings
    )
