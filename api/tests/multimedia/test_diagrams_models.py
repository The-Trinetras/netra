from uuid import uuid4

from netra_api.multimedia.diagrams.models import (
    DiagramEdge,
    DiagramLayer,
    DiagramNode,
    DiagramNodeType,
    DiagramStructure,
)
from netra_api.multimedia.figures.evidence import FigureEvidenceReference


def test_diagram_structure_carries_layers_and_reading_order():
    reference = FigureEvidenceReference(
        evidence_id="ev-1", source_version_id=uuid4(), locator="figure-2", figure_index=1
    )
    node_a = DiagramNode(node_id="n1", node_type=DiagramNodeType.SHAPE, label="Start")
    node_b = DiagramNode(node_id="n2", node_type=DiagramNodeType.SHAPE, label="End")
    edge = DiagramEdge(source_node_id="n1", target_node_id="n2", label="leads to")
    layer = DiagramLayer(name="flow", ordinal=0, nodes=[node_a, node_b], edges=[edge])

    structure = DiagramStructure(
        diagram_id=uuid4(), reference=reference, reading_order=["n1", "n2"], layers=[layer]
    )

    assert structure.layers[0].edges[0].directed is True
    assert structure.reading_order == ["n1", "n2"]


def test_diagram_node_defaults_to_no_parent():
    node = DiagramNode(node_id="n1", node_type=DiagramNodeType.GROUP, label="Cycle")
    assert node.parent_node_id is None
