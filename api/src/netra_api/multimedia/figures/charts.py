"""Chart/graph structure — axes, scales, units, series and plotted values.

A netra_api.multimedia.figures.models.FigureDescription answers "what is
this picture of". It cannot answer the AgentSpec's actual question:
"How does that line show constant resistance, and where does the table
show the same thing?" That needs the axes, their units and the plotted
values as addressable data, not as prose — which is precisely the gap
the walkthrough's transcript has when it says "this line" and never
names the axes.

ChartStructure is that data. Like DiagramStructure it is a figure that
has been structurally decomposed, so it reuses FigureEvidenceReference
rather than introducing a second evidence path.

Every readable detail carries its own ObservationSource, because the
difference between an axis label that was legible and one that was
reconstructed decides whether Netra may state "current in amperes" as
a fact. axes_established exists so a caller can ask that question once,
in one place, instead of each consumer re-deriving it and one of them
getting it wrong.
"""

from __future__ import annotations

from enum import Enum
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from netra_api.multimedia.evidence import ObservationSource
from netra_api.multimedia.figures.evidence import FigureEvidenceReference
from netra_api.multimedia.observations import (
    ObservationInvariantError,
    check_observation,
    is_authoritative,
)


class AxisOrientation(str, Enum):
    X = "x"
    Y = "y"


class AxisScale(str, Enum):
    LINEAR = "linear"
    LOGARITHMIC = "logarithmic"
    CATEGORICAL = "categorical"
    UNKNOWN = "unknown"
    """The scale could not be determined from the figure. Not a synonym
    for LINEAR: assuming linearity is how a log plot silently becomes a
    wrong claim about proportionality."""


class ChartAxis(BaseModel):
    """One axis of a chart, with its label, unit, scale and range."""

    axis_id: str
    """Stable within the chart, so navigation survives re-description."""
    orientation: AxisOrientation
    label: Optional[str] = None
    label_source: ObservationSource = ObservationSource.OBSERVED
    unit: Optional[str] = None
    unit_source: ObservationSource = ObservationSource.OBSERVED
    """Units are carried separately from the label because they are
    checked separately: the AgentSpec's acceptance question fails a run
    for wrong units even when the quantity name is right."""
    scale: AxisScale = AxisScale.UNKNOWN
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    range_source: ObservationSource = ObservationSource.ESTIMATED
    """Axis bounds read off tick marks are estimates unless the endpoints
    themselves are labelled."""

    @model_validator(mode="after")
    def _check_observation_invariants(self) -> "ChartAxis":
        check_observation(f"{self.axis_id}.label", self.label_source, self.label)
        check_observation(f"{self.axis_id}.unit", self.unit_source, self.unit)
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            raise ValueError(f"{self.axis_id}: minimum must be <= maximum")
        return self

    @property
    def is_established(self) -> bool:
        """Whether this axis may be stated to a student as a fact.

        Requires an observed quantity label. A unit that was not printed
        on the axis is allowed to be absent, but a unit that was *read
        wrong* is caught by source validation, not here.
        """

        return is_authoritative(self.label_source) and self.label is not None


class ChartPoint(BaseModel):
    """One plotted value, with its own observation classification.

    A point whose coordinates were read off gridlines rather than from a
    printed data label is ESTIMATED. Presenting it as exact is the
    failure mode this field exists to prevent.
    """

    point_id: str
    x: Optional[float] = None
    y: Optional[float] = None
    value_source: ObservationSource = ObservationSource.ESTIMATED
    label: Optional[str] = None
    label_source: ObservationSource = ObservationSource.GENERATED

    @model_validator(mode="after")
    def _check_observation_invariants(self) -> "ChartPoint":
        # value_source classifies the x/y pair as a unit, which
        # check_observation's single-field signature does not cover, so
        # the unreadable rule is applied to the pair here — plus the
        # pair's own rule, that half a coordinate is not a point.
        if self.value_source is ObservationSource.UNREADABLE:
            if self.x is not None or self.y is not None:
                raise ObservationInvariantError(
                    f"{self.point_id}: coordinates marked unreadable must stay empty"
                )
        elif (self.x is None) != (self.y is None):
            raise ObservationInvariantError(
                f"{self.point_id}: a plotted point needs both coordinates or neither"
            )
        check_observation(f"{self.point_id}.label", self.label_source, self.label)
        return self


class ChartSeries(BaseModel):
    """One line/bar/scatter series within a chart."""

    series_id: str
    label: Optional[str] = None
    label_source: ObservationSource = ObservationSource.OBSERVED
    points: List[ChartPoint] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_observation_invariants(self) -> "ChartSeries":
        check_observation(f"{self.series_id}.label", self.label_source, self.label)
        point_ids = [point.point_id for point in self.points]
        if len(set(point_ids)) != len(point_ids):
            raise ValueError(f"{self.series_id}: point_id values must be unique")
        return self


class ChartKind(str, Enum):
    LINE = "line"
    SCATTER = "scatter"
    BAR = "bar"
    OTHER = "other"
    UNKNOWN = "unknown"


class ChartStructure(BaseModel):
    """A chart decomposed into axes and plotted series, anchored to its source."""

    chart_id: UUID
    reference: FigureEvidenceReference
    kind: ChartKind = ChartKind.UNKNOWN
    title: Optional[str] = None
    title_source: ObservationSource = ObservationSource.OBSERVED
    axes: List[ChartAxis] = Field(default_factory=list)
    series: List[ChartSeries] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_structure(self) -> "ChartStructure":
        check_observation("title", self.title_source, self.title)
        axis_ids = [axis.axis_id for axis in self.axes]
        if len(set(axis_ids)) != len(axis_ids):
            raise ValueError("axis_id values must be unique within a chart")
        orientations = [axis.orientation for axis in self.axes]
        if len(set(orientations)) != len(orientations):
            raise ValueError("a chart may declare at most one axis per orientation")
        series_ids = [series.series_id for series in self.series]
        if len(set(series_ids)) != len(series_ids):
            raise ValueError("series_id values must be unique within a chart")
        return self

    def axis(self, orientation: AxisOrientation) -> Optional[ChartAxis]:
        for candidate in self.axes:
            if candidate.orientation is orientation:
                return candidate
        return None

    @property
    def axes_established(self) -> bool:
        """Whether both axes were actually read off the figure.

        This is the check the AgentSpec's step 3/4 turns on: a transcript
        that says "this line" leaves this False, and only visual evidence
        with observed axis labels makes it True. A caller must not claim
        a relationship between the plotted quantities while it is False.
        """

        x_axis = self.axis(AxisOrientation.X)
        y_axis = self.axis(AxisOrientation.Y)
        if x_axis is None or y_axis is None:
            return False
        return x_axis.is_established and y_axis.is_established

    def unreadable_axis_ids(self) -> List[str]:
        """Axes whose label was present in the figure but could not be read."""

        return [
            axis.axis_id
            for axis in self.axes
            if axis.label_source is ObservationSource.UNREADABLE
        ]
