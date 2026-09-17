"""Validate an extracted EquationTree against the original equation.

multimedia.md: "Successful syntax conversion does not prove the source
was read correctly. Check signs, exponents, fractions, grouping and
units against the source." A tree that parses is only evidence that
*something* parsed; `V = I / R` is as well-formed as `V = I × R` and
means something different.

canonical_form is how grouping is checked without depending on prose.
It renders the tree as a fully parenthesized symbol string, so a lost
grouping changes the string even when every symbol survives:

    ((a + b) × c)   vs   (a + (b × c))

Both contain the same symbols in the same order. Only the parentheses
tell them apart, which is exactly why the AgentSpec counts "silently
lost grouping" as a failure.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

from netra_api.multimedia.equations.models import (
    EquationNode,
    EquationNodeKind,
    EquationTree,
)
from netra_api.multimedia.evidence import ObservationSource
from netra_api.multimedia.validation import (
    SourceCheckStatus,
    ValidationFinding,
    ValidationReport,
    finding,
)


class EquationSourceCheck(BaseModel):
    """A reviewer's record of the original equation.

    canonical_form is written by reading the equation in the source and
    rendering it the way canonical_form() below renders a tree. Storing
    the expected string rather than an expected tree keeps the reviewer's
    job small and keeps the comparison independent of how extraction
    happened to shape its nodes.
    """

    equation_index: int = Field(ge=0)
    canonical_form: Optional[str] = None
    units_by_symbol: dict[str, str] = Field(default_factory=dict)
    """Unit each operand symbol carries in the source, e.g. {"V": "V", "I": "A"}."""
    unreadable_in_source: bool = False


def canonical_form(node: EquationNode) -> str:
    """Render one node as a fully parenthesized symbol string.

    Every composite node is parenthesized, including ones whose grouping
    a human would consider obvious. Relying on precedence to omit
    parentheses would mean two trees with different structure could
    render identically, which defeats the comparison.

    A node with no symbol renders as "?" rather than being skipped: an
    operand extraction could not read is a visible hole in the string,
    not a silently shorter equation.
    """

    symbol = node.symbol if node.symbol is not None else "?"

    if not node.children:
        return symbol

    rendered = [canonical_form(child) for child in node.children]

    if node.kind is EquationNodeKind.FRACTION:
        if len(rendered) != 2:
            raise ValueError(f"{node.node_id}: a fraction needs exactly two children")
        return f"({rendered[0]} / {rendered[1]})"
    if node.kind is EquationNodeKind.SUPERSCRIPT:
        if len(rendered) != 2:
            raise ValueError(f"{node.node_id}: a superscript needs a base and an exponent")
        return f"({rendered[0]} ^ {rendered[1]})"
    if node.kind is EquationNodeKind.SUBSCRIPT:
        if len(rendered) != 2:
            raise ValueError(f"{node.node_id}: a subscript needs a base and an index")
        return f"({rendered[0]} _ {rendered[1]})"
    if node.kind is EquationNodeKind.FUNCTION:
        arguments = ", ".join(rendered)
        return f"{symbol}({arguments})"
    if node.kind is EquationNodeKind.GROUP:
        return "(" + " ".join(rendered) + ")"
    if node.kind is EquationNodeKind.OPERATOR:
        joined = f" {symbol} ".join(rendered)
        return f"({joined})"

    # An OPERAND that nonetheless has children is malformed rather than
    # merely unusual: nothing knows how to read it aloud or check it.
    raise ValueError(f"{node.node_id}: kind {node.kind.value} must not have children")


def walk(node: EquationNode) -> List[EquationNode]:
    """Every node of the subtree, parents before children."""

    nodes = [node]
    for child in node.children:
        nodes.extend(walk(child))
    return nodes


def check_node_ids_unique(tree: EquationTree) -> None:
    """Reject a tree that cannot be navigated unambiguously.

    Math-part navigation addresses nodes by node_id (multimedia.md:
    "Math-part navigation follows a deterministic structure"). Duplicate
    ids make "step into the second term" resolve to two different places
    on two different requests.
    """

    node_ids = [node.node_id for node in walk(tree.root)]
    if len(set(node_ids)) != len(node_ids):
        raise ValueError("node_id values must be unique within an equation tree")


def validate_equation_against_source(
    tree: EquationTree, expectation: EquationSourceCheck
) -> ValidationReport:
    """Compare tree's structure and units against the original equation."""

    check_node_ids_unique(tree)
    report = ValidationReport(object_id=str(tree.equation_id))

    if tree.reference.equation_index != expectation.equation_index:
        report.findings.append(
            finding(
                part_id=str(tree.equation_id),
                field="equation_index",
                expected=expectation.equation_index,
                extracted=tree.reference.equation_index,
                detail="extraction was checked against a different equation",
            )
        )

    report.findings.append(
        finding(
            part_id=str(tree.equation_id),
            field="canonical_form",
            expected=expectation.canonical_form,
            extracted=canonical_form(tree.root),
            unreadable_in_source=expectation.unreadable_in_source,
            detail="covers symbols, signs, exponents, fractions and grouping",
        )
    )
    report.findings.extend(_unit_findings(tree, expectation))
    report.findings.extend(_unreadable_node_findings(tree))
    return report


def _unit_findings(
    tree: EquationTree, expectation: EquationSourceCheck
) -> List[ValidationFinding]:
    findings: List[ValidationFinding] = []
    by_symbol = {
        node.symbol: node for node in walk(tree.root) if node.symbol is not None
    }
    for symbol, expected_unit in sorted(expectation.units_by_symbol.items()):
        node = by_symbol.get(symbol)
        if node is None:
            findings.append(
                finding(
                    part_id=f"symbol:{symbol}",
                    field="presence",
                    expected="present",
                    extracted=None,
                    detail="the source equation uses this symbol but extraction lost it",
                )
            )
            continue
        findings.append(
            finding(
                part_id=node.node_id,
                field="unit",
                expected=expected_unit,
                extracted=node.unit,
                unreadable_in_source=expectation.unreadable_in_source,
            )
        )
    return findings


def _unreadable_node_findings(tree: EquationTree) -> List[ValidationFinding]:
    """Report every node extraction itself flagged as unreadable.

    These are not mismatches: extraction was honest about not reading
    them. They still have to reach the report, because a tree containing
    one cannot be presented as a complete reading of the equation.
    """

    return [
        ValidationFinding(
            part_id=node.node_id,
            field="symbol",
            status=SourceCheckStatus.UNREADABLE_IN_SOURCE,
            extracted=None,
            detail="extraction could not read this part of the equation",
        )
        for node in walk(tree.root)
        if node.symbol is None
    ]


def source_verified_tree(
    tree: EquationTree, report: ValidationReport
) -> EquationTree:
    """Return tree marked OBSERVED, or unchanged when the check did not pass.

    This is the only supported way an EquationTree's extraction_source
    moves off GENERATED (multimedia.md: "Unverified extraction must not
    become a confident authoritative derivation"). Callers cannot flip
    the field themselves and then claim it was checked, because the
    report is what decides.
    """

    if not report.is_source_verified:
        return tree
    return tree.model_copy(update={"extraction_source": ObservationSource.OBSERVED})
