"""Equation domain models — navigable tree representation for blind users.

Equation processing is a bounded tool, not an agent (CLAUDE.md
"Architecture: only two agents"). An EquationTree exposes an equation's
structure (operators, operands, groupings) as a navigable tree rather
than one flattened spoken string, so a screen-reader client can step
into/out of sub-expressions on request. Always anchored to source
evidence via EquationEvidenceReference (CLAUDE.md "Evidence rules").
"""

from __future__ import annotations

from enum import Enum
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from netra_api.multimedia.evidence import ObservationSource, VisualEvidenceReference


class EquationNodeKind(str, Enum):
    OPERATOR = "operator"
    OPERAND = "operand"
    FUNCTION = "function"
    FRACTION = "fraction"
    GROUP = "group"
    SUPERSCRIPT = "superscript"
    SUBSCRIPT = "subscript"


class EquationNode(BaseModel):
    """One node of an equation's navigable expression tree."""

    node_id: str
    kind: EquationNodeKind
    spoken_form: str
    """How this node/subtree should be read aloud, e.g. "x squared"."""
    children: List["EquationNode"] = Field(default_factory=list)


EquationNode.model_rebuild()


class EquationEvidenceReference(VisualEvidenceReference):
    """Anchors an EquationTree to its source equation's locator."""

    equation_index: int = Field(ge=0)


class EquationTree(BaseModel):
    """A fully structured, navigable representation of one equation."""

    equation_id: UUID
    reference: EquationEvidenceReference
    root: EquationNode
    extraction_source: ObservationSource = ObservationSource.GENERATED
    """How the tree was obtained. multimedia.md: "Successful syntax
    conversion does not prove the source was read correctly" and
    "Unverified extraction must not become a confident authoritative
    derivation." A tree parsed from model output is GENERATED until signs,
    exponents, grouping and units have been checked against the source
    crop."""
    mathml: Optional[str] = None
    """Optional MathML rendering (see netra_api.multimedia.equations.mathml),
    kept alongside the navigable tree rather than instead of it."""
