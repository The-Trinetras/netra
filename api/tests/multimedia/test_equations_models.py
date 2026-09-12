from uuid import uuid4

import pytest

from netra_api.multimedia.equations.mathml import render_mathml
from netra_api.multimedia.equations.models import (
    EquationEvidenceReference,
    EquationNode,
    EquationNodeKind,
    EquationTree,
)


def _reference():
    return EquationEvidenceReference(
        evidence_id="ev-1", source_version_id=uuid4(), locator="eq-1", equation_index=0
    )


def test_equation_node_nests_children():
    child = EquationNode(node_id="n2", kind=EquationNodeKind.OPERAND, spoken_form="x")
    root = EquationNode(
        node_id="n1", kind=EquationNodeKind.SUPERSCRIPT, spoken_form="x squared", children=[child]
    )
    assert root.children[0].spoken_form == "x"


def test_equation_tree_round_trips_through_model_dump():
    tree = EquationTree(
        equation_id=uuid4(),
        reference=_reference(),
        root=EquationNode(node_id="n1", kind=EquationNodeKind.OPERAND, spoken_form="x"),
    )
    assert EquationTree.model_validate(tree.model_dump()) == tree


def test_render_mathml_is_not_yet_implemented():
    tree = EquationTree(
        equation_id=uuid4(),
        reference=_reference(),
        root=EquationNode(node_id="n1", kind=EquationNodeKind.OPERAND, spoken_form="x"),
    )
    with pytest.raises(NotImplementedError):
        render_mathml(tree)
