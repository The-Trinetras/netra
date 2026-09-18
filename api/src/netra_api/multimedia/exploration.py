"""Layered, deterministic navigation over already-extracted structures.

multimedia.md: "Support layered descriptions: overview, parts,
relationships and detail" and "Navigation through an established
structure is deterministic application logic."

Both halves of that matter. The layering is what lets a screen-reader
user scan a figure the way a sighted reader does — a sentence first,
then the parts, then how they connect, then one part in full — instead
of hearing one paragraph. The determinism is what makes it usable: the
same request returns the same parts with the same IDs every time,
because every function here is pure arithmetic over a stored structure.

Nothing in this module calls a model. The prompt's constraint is
"without generating new authoritative labels on every request": a
re-described figure whose part IDs moved would strand a student
mid-navigation, and a re-generated label would quietly change what they
were told a moment ago. Labels come from the extraction that was
already validated; this module only selects and orders them.

Rendering these views for speech or NVDA belongs to M5, not here.
"""

from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field

from netra_api.multimedia.diagrams.models import DiagramStructure
from netra_api.multimedia.equations.models import EquationNode, EquationTree
from netra_api.multimedia.evidence import ObservationSource
from netra_api.multimedia.figures.charts import ChartStructure
from netra_api.multimedia.tables.models import TableStructure


class ExplorationLayer(str, Enum):
    OVERVIEW = "overview"
    """One orienting statement: what this object is and how big it is."""
    PARTS = "parts"
    """The addressable parts, in a stable reading order."""
    RELATIONSHIPS = "relationships"
    """How the parts connect. Empty is a real answer, not a failure."""
    DETAIL = "detail"
    """One named part in full. Requires part_id."""


class ExplorationPart(BaseModel):
    """One addressable part of a structure, as navigation sees it."""

    part_id: str
    """Stable across re-description: it comes from the stored structure."""
    label: Optional[str] = None
    label_source: ObservationSource = ObservationSource.OBSERVED
    detail: Optional[str] = None
    """Longer text for the DETAIL layer. None at coarser layers, so a
    PARTS response does not carry a paragraph per part."""


class ExplorationRelationship(BaseModel):
    """One connection between two parts."""

    source_part_id: str
    target_part_id: str
    label: Optional[str] = None
    label_source: ObservationSource = ObservationSource.OBSERVED
    observation_source: ObservationSource = ObservationSource.OBSERVED
    """Whether this connection was actually drawn/printed or inferred.
    multimedia.md: "Keep diagram connectivity distinct from visual
    proximity" — two parts near each other are not connected."""
    directed: bool = True


class ExplorationView(BaseModel):
    """One layer of one object, ready for an accessible client to present."""

    object_id: str
    layer: ExplorationLayer
    summary: Optional[str] = None
    parts: List[ExplorationPart] = Field(default_factory=list)
    relationships: List[ExplorationRelationship] = Field(default_factory=list)
    unreadable_part_ids: List[str] = Field(default_factory=list)
    """Parts present in the source that could not be read. Surfaced at
    every layer: a student navigating a figure needs to know a gap exists
    where they are, not only in a summary they may have skipped."""


class UnknownPartError(KeyError):
    """Raised when a DETAIL request names a part the structure does not have."""


def explore_chart(
    chart: ChartStructure,
    layer: ExplorationLayer,
    part_id: Optional[str] = None,
) -> ExplorationView:
    """Navigate a chart: axes and series are its parts, plotted values its detail."""

    object_id = str(chart.chart_id)
    parts = _chart_parts(chart)
    unreadable = _unreadable_ids(parts)

    if layer is ExplorationLayer.OVERVIEW:
        return ExplorationView(
            object_id=object_id,
            layer=layer,
            summary=_chart_summary(chart),
            unreadable_part_ids=unreadable,
        )
    if layer is ExplorationLayer.PARTS:
        return ExplorationView(
            object_id=object_id,
            layer=layer,
            parts=[part.model_copy(update={"detail": None}) for part in parts],
            unreadable_part_ids=unreadable,
        )
    if layer is ExplorationLayer.RELATIONSHIPS:
        return ExplorationView(
            object_id=object_id,
            layer=layer,
            relationships=_chart_relationships(chart),
            unreadable_part_ids=unreadable,
        )
    return ExplorationView(
        object_id=object_id,
        layer=layer,
        parts=[_require_part(parts, part_id)],
        unreadable_part_ids=unreadable,
    )


def explore_diagram(
    diagram: DiagramStructure,
    layer: ExplorationLayer,
    part_id: Optional[str] = None,
) -> ExplorationView:
    """Navigate a diagram: nodes are its parts, edges its relationships."""

    object_id = str(diagram.diagram_id)
    parts = _diagram_parts(diagram)
    unreadable = _unreadable_ids(parts)

    if layer is ExplorationLayer.OVERVIEW:
        node_count = sum(len(sub.nodes) for sub in diagram.layers)
        edge_count = sum(len(sub.edges) for sub in diagram.layers)
        return ExplorationView(
            object_id=object_id,
            layer=layer,
            summary=f"Diagram with {node_count} parts and {edge_count} connections.",
            unreadable_part_ids=unreadable,
        )
    if layer is ExplorationLayer.PARTS:
        return ExplorationView(
            object_id=object_id,
            layer=layer,
            parts=[part.model_copy(update={"detail": None}) for part in parts],
            unreadable_part_ids=unreadable,
        )
    if layer is ExplorationLayer.RELATIONSHIPS:
        return ExplorationView(
            object_id=object_id,
            layer=layer,
            relationships=_diagram_relationships(diagram),
            unreadable_part_ids=unreadable,
        )
    return ExplorationView(
        object_id=object_id,
        layer=layer,
        parts=[_require_part(parts, part_id)],
        unreadable_part_ids=unreadable,
    )


def explore_table(
    table: TableStructure,
    layer: ExplorationLayer,
    part_id: Optional[str] = None,
) -> ExplorationView:
    """Navigate a table: cells are its parts, header governance its relationships."""

    object_id = str(table.table_id)
    parts = _table_parts(table)
    unreadable = _unreadable_ids(parts)

    if layer is ExplorationLayer.OVERVIEW:
        return ExplorationView(
            object_id=object_id,
            layer=layer,
            summary=(
                f"Table with {table.row_count} rows and {table.column_count} columns."
            ),
            unreadable_part_ids=unreadable,
        )
    if layer is ExplorationLayer.PARTS:
        return ExplorationView(
            object_id=object_id,
            layer=layer,
            parts=[part.model_copy(update={"detail": None}) for part in parts],
            unreadable_part_ids=unreadable,
        )
    if layer is ExplorationLayer.RELATIONSHIPS:
        return ExplorationView(
            object_id=object_id,
            layer=layer,
            relationships=_table_relationships(table),
            unreadable_part_ids=unreadable,
        )
    return ExplorationView(
        object_id=object_id,
        layer=layer,
        parts=[_require_part(parts, part_id)],
        unreadable_part_ids=unreadable,
    )


def explore_equation(
    tree: EquationTree,
    layer: ExplorationLayer,
    part_id: Optional[str] = None,
) -> ExplorationView:
    """Navigate an equation: nodes are its parts, parent/child its relationships."""

    object_id = str(tree.equation_id)
    parts = _equation_parts(tree.root)
    unreadable = _unreadable_ids(parts)

    if layer is ExplorationLayer.OVERVIEW:
        return ExplorationView(
            object_id=object_id,
            layer=layer,
            summary=tree.root.spoken_form,
            unreadable_part_ids=unreadable,
        )
    if layer is ExplorationLayer.PARTS:
        return ExplorationView(
            object_id=object_id,
            layer=layer,
            parts=[part.model_copy(update={"detail": None}) for part in parts],
            unreadable_part_ids=unreadable,
        )
    if layer is ExplorationLayer.RELATIONSHIPS:
        return ExplorationView(
            object_id=object_id,
            layer=layer,
            relationships=_equation_relationships(tree.root),
            unreadable_part_ids=unreadable,
        )
    return ExplorationView(
        object_id=object_id,
        layer=layer,
        parts=[_require_part(parts, part_id)],
        unreadable_part_ids=unreadable,
    )


def _chart_summary(chart: ChartStructure) -> str:
    """One orienting sentence, assembled only from what was actually read.

    An axis whose label could not be read is named as unread rather than
    omitted: "a chart of voltage against something" is a worse answer
    than saying the other axis is illegible, and omitting it entirely
    would let the student assume the axis is missing from the figure.
    """

    parts: List[str] = []
    for axis in sorted(chart.axes, key=lambda item: item.orientation.value):
        if axis.label_source is ObservationSource.UNREADABLE:
            parts.append(f"{axis.orientation.value} axis label could not be read")
        elif axis.label is None:
            parts.append(f"{axis.orientation.value} axis is unlabelled")
        else:
            unit = f" in {axis.unit}" if axis.unit else ""
            parts.append(f"{axis.orientation.value} axis is {axis.label}{unit}")
    axes_text = "; ".join(parts) if parts else "no axes were extracted"
    return f"{chart.kind.value} chart with {len(chart.series)} series: {axes_text}."


def _chart_parts(chart: ChartStructure) -> List[ExplorationPart]:
    parts: List[ExplorationPart] = []
    for axis in sorted(chart.axes, key=lambda item: item.orientation.value):
        unit = f" ({axis.unit})" if axis.unit else ""
        parts.append(
            ExplorationPart(
                part_id=axis.axis_id,
                label=None if axis.label is None else f"{axis.label}{unit}",
                label_source=axis.label_source,
                detail=_axis_detail(axis),
            )
        )
    for series in chart.series:
        parts.append(
            ExplorationPart(
                part_id=series.series_id,
                label=series.label,
                label_source=series.label_source,
                detail=_series_detail(series),
            )
        )
    return parts


def _axis_detail(axis) -> str:
    bounds = (
        f" from {axis.minimum} to {axis.maximum}"
        if axis.minimum is not None and axis.maximum is not None
        else ""
    )
    return f"{axis.orientation.value} axis, {axis.scale.value} scale{bounds}."


def _series_detail(series) -> str:
    readable = [
        f"({point.x}, {point.y})"
        for point in series.points
        if point.x is not None and point.y is not None
    ]
    unread = len(series.points) - len(readable)
    text = f"{len(series.points)} plotted values"
    if readable:
        text += ": " + ", ".join(readable)
    if unread:
        text += f"; {unread} could not be read"
    return text + "."


def _chart_relationships(chart: ChartStructure) -> List[ExplorationRelationship]:
    """Each series is plotted against each axis. Nothing more is claimed.

    A trend — "voltage rises in proportion to current" — is an
    interpretation of the plotted values, not a relationship read off the
    figure, so it is not manufactured here.
    """

    relationships: List[ExplorationRelationship] = []
    for series in chart.series:
        for axis in sorted(chart.axes, key=lambda item: item.orientation.value):
            relationships.append(
                ExplorationRelationship(
                    source_part_id=series.series_id,
                    target_part_id=axis.axis_id,
                    label=f"plotted against the {axis.orientation.value} axis",
                    label_source=ObservationSource.OBSERVED,
                    observation_source=axis.label_source,
                    directed=True,
                )
            )
    return relationships


def _diagram_parts(diagram: DiagramStructure) -> List[ExplorationPart]:
    """Nodes in the stored reading order, with any unlisted nodes appended.

    reading_order is the recommended traversal, but a node missing from
    it is still part of the diagram. Dropping it would make a part
    unreachable by navigation and invisible to a student.
    """

    by_id = {
        node.node_id: node for sub_layer in diagram.layers for node in sub_layer.nodes
    }
    ordered_ids = [node_id for node_id in diagram.reading_order if node_id in by_id]
    ordered_ids.extend(node_id for node_id in by_id if node_id not in ordered_ids)
    return [
        ExplorationPart(
            part_id=node_id,
            label=by_id[node_id].label,
            label_source=by_id[node_id].label_source,
            detail=by_id[node_id].description,
        )
        for node_id in ordered_ids
    ]


def _diagram_relationships(diagram: DiagramStructure) -> List[ExplorationRelationship]:
    return [
        ExplorationRelationship(
            source_part_id=edge.source_node_id,
            target_part_id=edge.target_node_id,
            label=edge.label,
            label_source=edge.label_source,
            observation_source=edge.connectivity_source,
            directed=edge.directed,
        )
        for sub_layer in diagram.layers
        for edge in sub_layer.edges
    ]


def _table_parts(table: TableStructure) -> List[ExplorationPart]:
    parts: List[ExplorationPart] = [
        ExplorationPart(
            part_id=header.header_id,
            label=header.text,
            label_source=header.text_source,
            detail=_header_detail(header),
        )
        for header in sorted(
            table.headers, key=lambda item: (item.orientation.value, item.index)
        )
    ]
    parts.extend(
        ExplorationPart(
            part_id=cell.cell_id,
            label=cell.text,
            label_source=cell.text_source,
            detail=_cell_detail(table, cell),
        )
        for cell in sorted(table.cells, key=lambda item: (item.row_index, item.column_index))
    )
    return parts


def _header_detail(header) -> str:
    unit = f", unit {header.unit}" if header.unit else ""
    span = f", spanning {header.span}" if header.span > 1 else ""
    return f"{header.orientation.value} header at index {header.index}{span}{unit}."


def _cell_detail(table: TableStructure, cell) -> str:
    governing = [
        header.text for header in table.headers_for(cell) if header.text is not None
    ]
    location = f"row {cell.row_index}, column {cell.column_index}"
    if not governing:
        return f"{location}."
    return f"{location}, under " + " and ".join(governing) + "."


def _table_relationships(table: TableStructure) -> List[ExplorationRelationship]:
    """Which header governs which cell.

    This is the relationship a prose summary destroys, and the one the
    AgentSpec's student needs when she asks where the table shows the
    same thing as the graph.
    """

    relationships: List[ExplorationRelationship] = []
    for cell in sorted(table.cells, key=lambda item: (item.row_index, item.column_index)):
        for header in table.headers_for(cell):
            relationships.append(
                ExplorationRelationship(
                    source_part_id=header.header_id,
                    target_part_id=cell.cell_id,
                    label="governs",
                    label_source=ObservationSource.OBSERVED,
                    observation_source=header.text_source,
                    directed=True,
                )
            )
    return relationships


def _equation_parts(root: EquationNode) -> List[ExplorationPart]:
    parts: List[ExplorationPart] = []
    stack = [root]
    while stack:
        node = stack.pop(0)
        unit = f" in {node.unit}" if node.unit else ""
        parts.append(
            ExplorationPart(
                part_id=node.node_id,
                label=node.spoken_form,
                label_source=(
                    ObservationSource.UNREADABLE
                    if node.symbol is None
                    else ObservationSource.OBSERVED
                ),
                detail=f"{node.kind.value}: {node.spoken_form}{unit}.",
            )
        )
        stack = list(node.children) + stack
    return parts


def _equation_relationships(root: EquationNode) -> List[ExplorationRelationship]:
    relationships: List[ExplorationRelationship] = []
    stack = [root]
    while stack:
        node = stack.pop(0)
        for child in node.children:
            relationships.append(
                ExplorationRelationship(
                    source_part_id=node.node_id,
                    target_part_id=child.node_id,
                    label="contains",
                    label_source=ObservationSource.OBSERVED,
                    observation_source=ObservationSource.OBSERVED,
                    directed=True,
                )
            )
        stack = list(node.children) + stack
    return relationships


def _unreadable_ids(parts: List[ExplorationPart]) -> List[str]:
    return [
        part.part_id
        for part in parts
        if part.label_source is ObservationSource.UNREADABLE
    ]


def _require_part(parts: List[ExplorationPart], part_id: Optional[str]) -> ExplorationPart:
    if part_id is None:
        raise UnknownPartError("the detail layer requires a part_id")
    for part in parts:
        if part.part_id == part_id:
            return part
    raise UnknownPartError(part_id)
