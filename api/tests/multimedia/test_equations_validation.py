"""Equation canonical form, unit checks and the source-verified gate.

multimedia.md: "Successful syntax conversion does not prove the source
was read correctly. Check signs, exponents, fractions, grouping and
units against the source." Every one of those is exercised here.
"""

from uuid import uuid4

import pytest

from fixtures.ohm_law import (
    equation_reference,
    equation_source_check,
    extracted_equation,
)
from netra_api.multimedia.equations.models import (
    EquationNode,
    EquationNodeKind,
    EquationTree,
)
from netra_api.multimedia.equations.validation import (
    canonical_form,
    check_node_ids_unique,
    source_verified_tree,
    validate_equation_against_source,
    walk,
)
from netra_api.multimedia.evidence import ObservationSource


def _operand(node_id: str, symbol: str) -> EquationNode:
    return EquationNode(
        node_id=node_id, kind=EquationNodeKind.OPERAND, symbol=symbol, spoken_form=symbol
    )


def _tree(root: EquationNode) -> EquationTree:
    return EquationTree(equation_id=uuid4(), reference=equation_reference(), root=root)


def test_canonical_form_renders_the_fixture_equation():
    assert canonical_form(extracted_equation().root) == "(V = (I × R))"


def test_canonical_form_distinguishes_grouping():
    """The AgentSpec fails a run for "silently lost grouping"."""

    grouped_left = EquationNode(
        node_id="n0",
        kind=EquationNodeKind.OPERATOR,
        symbol="×",
        spoken_form="a plus b, times c",
        children=[
            EquationNode(
                node_id="n1",
                kind=EquationNodeKind.OPERATOR,
                symbol="+",
                spoken_form="a plus b",
                children=[_operand("n2", "a"), _operand("n3", "b")],
            ),
            _operand("n4", "c"),
        ],
    )
    grouped_right = EquationNode(
        node_id="n0",
        kind=EquationNodeKind.OPERATOR,
        symbol="+",
        spoken_form="a plus b times c",
        children=[
            _operand("n2", "a"),
            EquationNode(
                node_id="n1",
                kind=EquationNodeKind.OPERATOR,
                symbol="×",
                spoken_form="b times c",
                children=[_operand("n3", "b"), _operand("n4", "c")],
            ),
        ],
    )

    assert canonical_form(grouped_left) != canonical_form(grouped_right)


def test_canonical_form_keeps_exponents_and_fractions_distinct():
    power = EquationNode(
        node_id="n0",
        kind=EquationNodeKind.SUPERSCRIPT,
        spoken_form="x squared",
        children=[_operand("n1", "x"), _operand("n2", "2")],
    )
    fraction = EquationNode(
        node_id="n0",
        kind=EquationNodeKind.FRACTION,
        spoken_form="x over 2",
        children=[_operand("n1", "x"), _operand("n2", "2")],
    )

    assert canonical_form(power) == "(x ^ 2)"
    assert canonical_form(fraction) == "(x / 2)"


def test_canonical_form_keeps_signs():
    positive = _operand("n1", "2")
    negative = _operand("n1", "-2")

    assert canonical_form(positive) != canonical_form(negative)


def test_an_unread_operand_leaves_a_visible_hole():
    """A symbol extraction could not read must not shorten the equation."""

    root = EquationNode(
        node_id="n0",
        kind=EquationNodeKind.OPERATOR,
        symbol="=",
        spoken_form="V equals something",
        children=[
            _operand("n1", "V"),
            EquationNode(node_id="n2", kind=EquationNodeKind.OPERAND, spoken_form="unreadable"),
        ],
    )

    assert canonical_form(root) == "(V = ?)"


def test_a_fraction_needs_exactly_two_children():
    malformed = EquationNode(
        node_id="n0",
        kind=EquationNodeKind.FRACTION,
        spoken_form="broken",
        children=[_operand("n1", "x")],
    )

    with pytest.raises(ValueError):
        canonical_form(malformed)


def test_an_operand_with_children_is_malformed():
    malformed = EquationNode(
        node_id="n0",
        kind=EquationNodeKind.OPERAND,
        symbol="x",
        spoken_form="x",
        children=[_operand("n1", "y")],
    )

    with pytest.raises(ValueError):
        canonical_form(malformed)


def test_correct_extraction_is_source_verified():
    report = validate_equation_against_source(
        extracted_equation(), equation_source_check()
    )

    assert report.mismatches == []
    assert report.is_source_verified is True


def test_a_multiplication_read_as_a_division_fails_the_check():
    """Both parse. Only one is the equation in the document."""

    report = validate_equation_against_source(
        extracted_equation(operator="/", spoken_operator="over"), equation_source_check()
    )

    assert report.is_source_verified is False
    mismatch = next(f for f in report.mismatches if f.field == "canonical_form")
    assert mismatch.extracted == "(V = (I / R))"


def test_a_lost_unit_is_reported():
    tree = extracted_equation()
    without_unit = _tree(
        tree.root.model_copy(
            update={
                "children": [
                    tree.root.children[0].model_copy(update={"unit": None}),
                    tree.root.children[1],
                ]
            }
        )
    )

    report = validate_equation_against_source(without_unit, equation_source_check())

    assert report.is_source_verified is False
    assert any(f.field == "unit" and f.extracted is None for f in report.mismatches)


def test_a_lost_symbol_is_reported_as_missing():
    check = equation_source_check().model_copy(
        update={"units_by_symbol": {"V": "V", "I": "A", "R": "ohm", "P": "W"}}
    )

    report = validate_equation_against_source(extracted_equation(), check)

    assert any(
        f.part_id == "symbol:P" and f.field == "presence" for f in report.mismatches
    )


def test_an_unreadable_equation_states_a_limitation_rather_than_passing():
    check = equation_source_check().model_copy(update={"unreadable_in_source": True})

    report = validate_equation_against_source(extracted_equation(), check)

    assert report.is_source_verified is False
    assert report.mismatches == []
    assert report.unreadable


def test_duplicate_node_ids_are_rejected():
    root = EquationNode(
        node_id="n0",
        kind=EquationNodeKind.OPERATOR,
        symbol="+",
        spoken_form="x plus x",
        children=[_operand("n1", "x"), _operand("n1", "x")],
    )

    with pytest.raises(ValueError):
        check_node_ids_unique(_tree(root))


def test_walk_visits_parents_before_children():
    node_ids = [node.node_id for node in walk(extracted_equation().root)]

    assert node_ids.index("eq01.root") < node_ids.index("eq01.product")
    assert node_ids.index("eq01.product") < node_ids.index("eq01.i")


def test_extraction_source_only_moves_to_observed_through_a_passing_report():
    tree = extracted_equation()
    assert tree.extraction_source is ObservationSource.GENERATED

    passing = validate_equation_against_source(tree, equation_source_check())
    failing = validate_equation_against_source(
        extracted_equation(operator="/"), equation_source_check()
    )

    assert source_verified_tree(tree, passing).extraction_source is ObservationSource.OBSERVED
    assert source_verified_tree(tree, failing).extraction_source is ObservationSource.GENERATED


def test_validation_against_the_wrong_equation_is_reported():
    report = validate_equation_against_source(
        extracted_equation(), equation_source_check().model_copy(update={"equation_index": 9})
    )

    assert any(f.field == "equation_index" for f in report.mismatches)
