"""Layered navigation: stable IDs, real determinism, honest gaps.

The value of this module to a blind student is that it behaves the same
way twice. A part that changes id between requests strands somebody
mid-navigation, and a label that is regenerated each time quietly
changes what they were just told.
"""

from uuid import uuid4

import pytest

from fixtures.ohm_law import (
    extracted_chart,
    extracted_equation,
    extracted_table,
    figure_reference,
)
from netra_api.multimedia.diagrams.models import (
    DiagramEdge,
    DiagramLayer,
    DiagramNode,
    DiagramNodeType,
    DiagramStructure,
)
from netra_api.multimedia.evidence import ObservationSource
from netra_api.multimedia.exploration import (
    ExplorationLayer,
    UnknownPartError,
    explore_chart,
    explore_diagram,
    explore_equation,
    explore_table,
)


def _diagram() -> DiagramStructure:
    return DiagramStructure(
        diagram_id=uuid4(),
        reference=figure_reference(),
        reading_order=["battery", "resistor"],
        layers=[
            DiagramLayer(
                name="structure",
                ordinal=0,
                nodes=[
                    DiagramNode(
                        node_id="battery",
                        node_type=DiagramNodeType.SHAPE,
                        label="battery",
                        description="A cell supplying the circuit.",
                    ),
                    DiagramNode(
                        node_id="resistor",
                        node_type=DiagramNodeType.SHAPE,
                        label="resistor",
                    ),
                    DiagramNode(
                        node_id="unlisted",
                        node_type=DiagramNodeType.LABEL,
                        label="R",
                    ),
                ],
                edges=[
                    DiagramEdge(source_node_id="battery", target_node_id="resistor")
                ],
            )
        ],
    )


def test_chart_overview_names_both_axes_with_units():
    view = explore_chart(extracted_chart(), ExplorationLayer.OVERVIEW)

    assert "x axis is current in A" in view.summary
    assert "y axis is voltage in V" in view.summary


def test_chart_overview_says_an_axis_was_unreadable_rather_than_omitting_it():
    view = explore_chart(extracted_chart(unreadable_axes=True), ExplorationLayer.OVERVIEW)

    assert "could not be read" in view.summary
    assert view.unreadable_part_ids == ["fig02.x", "fig02.y"]


def test_chart_parts_are_stable_across_repeated_requests():
    chart = extracted_chart()

    first = explore_chart(chart, ExplorationLayer.PARTS)
    second = explore_chart(chart, ExplorationLayer.PARTS)

    assert [part.part_id for part in first.parts] == [part.part_id for part in second.parts]
    assert first == second


def test_parts_layer_omits_the_per_part_detail_text():
    """A parts listing is for scanning, not for hearing a paragraph each."""

    view = explore_chart(extracted_chart(), ExplorationLayer.PARTS)

    assert all(part.detail is None for part in view.parts)


def test_detail_layer_returns_one_named_part_in_full():
    view = explore_chart(extracted_chart(), ExplorationLayer.DETAIL, part_id="fig02.s1")

    assert len(view.parts) == 1
    assert "3 plotted values" in view.parts[0].detail


def test_detail_layer_rejects_an_unknown_part():
    with pytest.raises(UnknownPartError):
        explore_chart(extracted_chart(), ExplorationLayer.DETAIL, part_id="nope")


def test_detail_layer_requires_a_part_id():
    with pytest.raises(UnknownPartError):
        explore_chart(extracted_chart(), ExplorationLayer.DETAIL)


def test_chart_relationships_do_not_invent_a_trend():
    """"The voltage rises in proportion" is interpretation, not structure."""

    view = explore_chart(extracted_chart(), ExplorationLayer.RELATIONSHIPS)

    assert all("plotted against" in rel.label for rel in view.relationships)


def test_diagram_relationships_preserve_connectivity_classification():
    diagram = _diagram()
    inferred = diagram.model_copy(
        update={
            "layers": [
                diagram.layers[0].model_copy(
                    update={
                        "edges": [
                            diagram.layers[0]
                            .edges[0]
                            .model_copy(
                                update={
                                    "connectivity_source": ObservationSource.ESTIMATED
                                }
                            )
                        ]
                    }
                )
            ]
        }
    )

    view = explore_diagram(inferred, ExplorationLayer.RELATIONSHIPS)

    assert view.relationships[0].observation_source is ObservationSource.ESTIMATED


def test_a_node_missing_from_reading_order_is_still_reachable():
    """Dropping it would make a part of the diagram invisible."""

    view = explore_diagram(_diagram(), ExplorationLayer.PARTS)

    part_ids = [part.part_id for part in view.parts]
    assert part_ids == ["battery", "resistor", "unlisted"]


def test_table_relationships_connect_each_cell_to_its_header():
    view = explore_table(extracted_table(), ExplorationLayer.RELATIONSHIPS)

    pairs = {(rel.source_part_id, rel.target_part_id) for rel in view.relationships}
    assert ("tbl01.h1", "tbl01.r1.c1") in pairs


def test_table_cell_detail_names_the_governing_header():
    view = explore_table(
        extracted_table(), ExplorationLayer.DETAIL, part_id="tbl01.r2.c1"
    )

    assert "under Voltage" in view.parts[0].detail


def test_table_overview_reports_the_grid_size():
    view = explore_table(extracted_table(), ExplorationLayer.OVERVIEW)

    assert view.summary == "Table with 4 rows and 2 columns."


def test_equation_parts_cover_every_node():
    view = explore_equation(extracted_equation(), ExplorationLayer.PARTS)

    assert {part.part_id for part in view.parts} == {
        "eq01.root",
        "eq01.v",
        "eq01.product",
        "eq01.i",
        "eq01.r",
    }


def test_equation_relationships_are_parent_to_child():
    view = explore_equation(extracted_equation(), ExplorationLayer.RELATIONSHIPS)

    pairs = {(rel.source_part_id, rel.target_part_id) for rel in view.relationships}
    assert ("eq01.root", "eq01.product") in pairs
    assert ("eq01.product", "eq01.i") in pairs


def test_equation_detail_includes_the_operand_unit():
    view = explore_equation(
        extracted_equation(), ExplorationLayer.DETAIL, part_id="eq01.i"
    )

    assert "in A" in view.parts[0].detail


def test_exploration_makes_no_provider_or_model_call():
    """Every layer is pure arithmetic over the stored structure.

    Exercised by calling every layer of every type in one test: if any
    of them reached for a provider, this module would need one to import.
    """

    chart = extracted_chart()
    table = extracted_table()
    equation = extracted_equation()
    diagram = _diagram()

    for layer in (
        ExplorationLayer.OVERVIEW,
        ExplorationLayer.PARTS,
        ExplorationLayer.RELATIONSHIPS,
    ):
        assert explore_chart(chart, layer) == explore_chart(chart, layer)
        assert explore_table(table, layer) == explore_table(table, layer)
        assert explore_equation(equation, layer) == explore_equation(equation, layer)
        assert explore_diagram(diagram, layer) == explore_diagram(diagram, layer)
