"""MathML rendering for equation trees.

Converts a netra_api.multimedia.equations.models.EquationTree into a
MathML string for clients that can render it, alongside — not instead
of — the navigable EquationNode tree the model already provides.
"""

from __future__ import annotations

from netra_api.multimedia.equations.models import EquationTree


def render_mathml(tree: EquationTree) -> str:
    """Render tree.root as a MathML ``<math>`` string.

    TODO: implement the EquationNodeKind -> MathML tag mapping (mo/mi/mn/
    mfrac/msup/msub/mrow). No product decision has been made yet on the
    exact MathML fidelity required, so this is intentionally left
    unimplemented rather than guessed (CLAUDE.md "Do not invent missing
    product requirements").
    """

    raise NotImplementedError("TODO: render_mathml — MathML tag mapping not implemented")
