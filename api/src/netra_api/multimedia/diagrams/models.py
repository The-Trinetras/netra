"""Diagram structure extraction — layered, navigable representation.

Diagram extraction is a bounded tool, not an agent (CLAUDE.md
"Architecture: only two agents"). Unlike a general FigureDescription, a
DiagramStructure exposes the diagram's layers explicitly (nodes, edges,
grouping, reading order) so a screen-reader client can navigate the
diagram's structure rather than hear one flattened paragraph. A diagram
is a figure that has been structurally decomposed, so it shares the
same FigureEvidenceReference anchor rather than introducing a second
evidence path (see netra_api.multimedia.figures.evidence).
"""

from __future__ import annotations

from enum import Enum
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from netra_api.multimedia.evidence import ObservationSource
from netra_api.multimedia.figures.evidence import FigureEvidenceReference


class DiagramNodeType(str, Enum):
    SHAPE = "shape"
    LABEL = "label"
    CONNECTOR_ENDPOINT = "connector_endpoint"
    GROUP = "group"


class DiagramNode(BaseModel):
    """One addressable element of a diagram's structural layer."""

    node_id: str
    node_type: DiagramNodeType
    label: Optional[str] = None
    label_source: ObservationSource = ObservationSource.OBSERVED
    """Whether label was legible in the diagram or reconstructed."""
    description: Optional[str] = None
    parent_node_id: Optional[str] = None
    """For GROUP membership; None at the top level."""


class DiagramEdge(BaseModel):
    """A connection between two DiagramNodes (e.g. an arrow or a line)."""

    source_node_id: str
    target_node_id: str
    label: Optional[str] = None
    label_source: ObservationSource = ObservationSource.OBSERVED
    connectivity_source: ObservationSource = ObservationSource.OBSERVED
    """Whether this connection was actually drawn in the diagram or
    inferred. multimedia.md: "Keep diagram connectivity distinct from
    visual proximity" — two shapes near each other are not an edge, and an
    inferred edge must not read as an observed one."""
    directed: bool = True


class DiagramLayer(BaseModel):
    """One ordered layer of a diagram (e.g. "structure", "labels", "flow")."""

    name: str
    ordinal: int = Field(ge=0)
    nodes: List[DiagramNode] = Field(default_factory=list)
    edges: List[DiagramEdge] = Field(default_factory=list)


class DiagramStructure(BaseModel):
    """A fully layered, navigable structural extraction of one diagram."""

    diagram_id: UUID
    reference: FigureEvidenceReference
    reading_order: List[str] = Field(default_factory=list)
    """node_id values in the recommended sequential-reading order for a
    screen-reader traversal."""
    layers: List[DiagramLayer] = Field(default_factory=list)
