"""Chart structure invariants and the axes-established gate.

Uses the AgentSpec acceptance fixture (see multimedia.fixtures.ohm_law,
which is labelled synthetic). These tests check the structure's own
rules; checking extraction against the original figure is
test_figures_validation.py.
"""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from fixtures.ohm_law import (
    MEASUREMENTS,
    extracted_chart,
    figure_reference,
)
from netra_api.multimedia.evidence import ObservationSource
from netra_api.multimedia.figures.charts import (
    AxisOrientation,
    AxisScale,
    ChartAxis,
    ChartPoint,
    ChartSeries,
    ChartStructure,
)
from netra_api.multimedia.observations import ObservationInvariantError


def test_fixture_chart_matches_the_agentspec_axes():
    chart = extracted_chart()

    x_axis = chart.axis(AxisOrientation.X)
    y_axis = chart.axis(AxisOrientation.Y)

    assert (x_axis.label, x_axis.unit) == ("current", "A")
    assert (y_axis.label, y_axis.unit) == ("voltage", "V")
    assert [(point.x, point.y) for point in chart.series[0].points] == list(MEASUREMENTS)


def test_axes_established_requires_both_observed_labels():
    assert extracted_chart().axes_established is True


def test_unreadable_axes_are_not_established_and_are_named():
    chart = extracted_chart(unreadable_axes=True)

    assert chart.axes_established is False
    assert chart.unreadable_axis_ids() == ["fig02.x", "fig02.y"]


def test_a_reconstructed_axis_label_does_not_establish_the_axis():
    """A guessed label is not an observation, however plausible it reads."""

    chart = ChartStructure(
        chart_id=uuid4(),
        reference=figure_reference(),
        axes=[
            ChartAxis(
                axis_id="x",
                orientation=AxisOrientation.X,
                label="current",
                label_source=ObservationSource.GENERATED,
            ),
            ChartAxis(
                axis_id="y",
                orientation=AxisOrientation.Y,
                label="voltage",
            ),
        ],
    )

    assert chart.axes_established is False


def test_a_chart_missing_an_axis_is_not_established():
    chart = ChartStructure(
        chart_id=uuid4(),
        reference=figure_reference(),
        axes=[ChartAxis(axis_id="x", orientation=AxisOrientation.X, label="current")],
    )

    assert chart.axes_established is False


def test_unreadable_axis_label_must_not_carry_a_value():
    with pytest.raises(ValidationError) as excinfo:
        ChartAxis(
            axis_id="x",
            orientation=AxisOrientation.X,
            label="current",
            label_source=ObservationSource.UNREADABLE,
        )

    assert "unreadable" in str(excinfo.value)


def test_an_observed_axis_with_no_printed_unit_is_allowed():
    """OBSERVED with no value means "looked, and there is none".

    A unitless axis is ordinary. Forcing a value here would make
    extraction invent a unit for every one of them.
    """

    axis = ChartAxis(
        axis_id="x",
        orientation=AxisOrientation.X,
        label="count",
        unit=None,
        unit_source=ObservationSource.OBSERVED,
    )

    assert axis.unit is None
    assert axis.is_established is True


def test_an_axis_whose_label_was_observed_absent_is_not_established():
    """An unlabelled axis cannot back a claim about what it measures."""

    axis = ChartAxis(
        axis_id="x",
        orientation=AxisOrientation.X,
        label=None,
        label_source=ObservationSource.OBSERVED,
    )

    assert axis.is_established is False


def test_unreadable_point_must_not_carry_coordinates():
    with pytest.raises(ValidationError):
        ChartPoint(
            point_id="p0", x=1.0, y=2.0, value_source=ObservationSource.UNREADABLE
        )


def test_observed_point_needs_both_coordinates():
    with pytest.raises(ValidationError):
        ChartPoint(point_id="p0", x=1.0, value_source=ObservationSource.OBSERVED)


def test_a_chart_rejects_two_axes_with_the_same_orientation():
    with pytest.raises(ValidationError):
        ChartStructure(
            chart_id=uuid4(),
            reference=figure_reference(),
            axes=[
                ChartAxis(axis_id="x1", orientation=AxisOrientation.X, label="current"),
                ChartAxis(axis_id="x2", orientation=AxisOrientation.X, label="time"),
            ],
        )


def test_a_chart_rejects_duplicate_series_ids():
    with pytest.raises(ValidationError):
        ChartStructure(
            chart_id=uuid4(),
            reference=figure_reference(),
            series=[
                ChartSeries(series_id="s1", label="a"),
                ChartSeries(series_id="s1", label="b"),
            ],
        )


def test_a_series_rejects_duplicate_point_ids():
    with pytest.raises(ValidationError):
        ChartSeries(
            series_id="s1",
            label="a",
            points=[
                ChartPoint(point_id="p0", x=1.0, y=2.0),
                ChartPoint(point_id="p0", x=2.0, y=4.0),
            ],
        )


def test_an_axis_rejects_an_inverted_range():
    with pytest.raises(ValidationError):
        ChartAxis(
            axis_id="x",
            orientation=AxisOrientation.X,
            label="current",
            minimum=4.0,
            maximum=0.0,
        )


def test_unknown_scale_is_not_treated_as_linear():
    """UNKNOWN must stay its own value; nothing may default it to LINEAR."""

    axis = ChartAxis(axis_id="x", orientation=AxisOrientation.X, label="current")

    assert axis.scale is AxisScale.UNKNOWN


def test_observation_invariant_error_is_a_value_error():
    """So pydantic reports it as validation rather than an internal failure."""

    assert issubclass(ObservationInvariantError, ValueError)
