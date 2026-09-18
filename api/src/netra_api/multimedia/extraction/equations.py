"""Parse a basic equation from parser output into a navigable EquationTree.

Scope is "basic equations" (current-scope.md): symbols, numbers, signs,
+ - × · / ÷, exponents, subscripts, parentheses and \\frac. Anything
beyond that (integrals, sums, matrices, unknown LaTeX commands) raises
ExtractionUnsupportedError instead of producing a guess.

The tree is exactly what the text says. Notation variants of the same
operator are normalized so canonical_form stays comparable with a
reviewer's record: '*', '\\times', '×' and implicit multiplication all
become '×'; '\\cdot' and '·' become '·'; U+2212 becomes '-'. Division by
'/' stays an operator '/', '\\frac' becomes a FRACTION and '÷' stays '÷',
because those are different notations a reviewer may need to check.

Associative chains of '+' or '×' are flattened into one n-ary operator
("(a + b + c)"); '-', '/' and '÷' nest to the left. A unary minus on a
number is part of the number ("-2"); on anything else it is a FUNCTION
node with symbol '-', so canonical_form renders "-(x)" and the sign can
never disappear.

Units are not read from the equation text: a crop of "V = I × R" does not
state them. Operands therefore carry unit=None, and a reviewer record
that expects units reports the loss as a mismatch — the extraction did
not capture them, which must not pass as verified.

The token '?' or the word 'unreadable' (as the parser's own marker)
becomes an OPERAND with symbol None: an honest hole that validation
reports as unreadable rather than a mismatch.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional
from uuid import UUID

from netra_api.multimedia.equations.models import (
    EquationEvidenceReference,
    EquationNode,
    EquationNodeKind,
    EquationTree,
)
from netra_api.multimedia.evidence import ObservationSource
from netra_api.multimedia.providers.errors import (
    ExtractionUnsupportedError,
    MalformedProviderResponseError,
)

EXTRACTION_PROVIDER = "document_parser"

_SPOKEN = {"=": "equals", "+": "plus", "-": "minus", "×": "times", "·": "times", "/": "divided by", "÷": "divided by"}
_MULTIPLY = {"*": "×", "×": "×", "\\times": "×", "·": "·", "\\cdot": "·"}
_KNOWN_COMMANDS = {"\\times", "\\cdot", "\\frac", "\\left", "\\right", "\\div"}
_GREEK = {
    "\\alpha": "α", "\\beta": "β", "\\gamma": "γ", "\\delta": "δ", "\\Delta": "Δ", "\\theta": "θ",
    "\\lambda": "λ", "\\mu": "μ", "\\pi": "π", "\\rho": "ρ", "\\sigma": "σ", "\\omega": "ω", "\\Omega": "Ω",
}

_TOKEN = re.compile(
    r"\s*(?:(?P<command>\\[A-Za-z]+)|(?P<number>\d+(?:\.\d+)?|\.\d+)|(?P<unreadable>\?|\[unreadable\]|unreadable)"
    r"|(?P<ident>[A-Za-zͰ-Ͽ])|(?P<op>[=+\-−*×·/÷^_(){}]))"
)


@dataclass
class _Token:
    kind: str
    value: str


def _tokenize(text: str) -> List[_Token]:
    source = text.strip().strip("$").strip()
    if source.startswith("\\[") and source.endswith("\\]"):
        source = source[2:-2]
    tokens: List[_Token] = []
    position = 0
    while position < len(source):
        if source[position].isspace():
            position += 1
            continue
        match = _TOKEN.match(source, position)
        if not match or match.end() == position:
            raise MalformedProviderResponseError(EXTRACTION_PROVIDER, "equation contains an unrecognised character", field="equation")
        position = match.end()
        if match.group("command"):
            command = match.group("command")
            if command in _GREEK:
                tokens.append(_Token("ident", _GREEK[command]))
            elif command == "\\div":
                tokens.append(_Token("op", "÷"))
            elif command in ("\\left", "\\right"):
                continue
            elif command in _KNOWN_COMMANDS:
                tokens.append(_Token("command", command))
            else:
                raise ExtractionUnsupportedError(EXTRACTION_PROVIDER, f"{command} is beyond basic-equation support")
        elif match.group("number"):
            tokens.append(_Token("number", match.group("number")))
        elif match.group("unreadable"):
            tokens.append(_Token("unreadable", "?"))
        elif match.group("ident"):
            tokens.append(_Token("ident", match.group("ident")))
        else:
            op = match.group("op")
            tokens.append(_Token("op", "-" if op == "−" else op))
    if not tokens:
        raise MalformedProviderResponseError(EXTRACTION_PROVIDER, "equation text is empty", field="equation")
    return tokens


class _Parser:
    def __init__(self, tokens: List[_Token], id_prefix: str) -> None:
        self._tokens = tokens
        self._position = 0
        self._prefix = id_prefix
        self._counter = 0

    # -- helpers ---------------------------------------------------------
    def _peek(self) -> Optional[_Token]:
        return self._tokens[self._position] if self._position < len(self._tokens) else None

    def _take(self) -> _Token:
        token = self._peek()
        if token is None:
            raise MalformedProviderResponseError(EXTRACTION_PROVIDER, "equation ends unexpectedly", field="equation")
        self._position += 1
        return token

    def _expect_op(self, value: str) -> None:
        token = self._take()
        if token.kind != "op" or token.value != value:
            raise MalformedProviderResponseError(EXTRACTION_PROVIDER, f"expected '{value}' in equation", field="equation")

    def _id(self) -> str:
        self._counter += 1
        return f"{self._prefix}.n{self._counter}"

    def _operand(self, symbol: Optional[str]) -> EquationNode:
        return EquationNode(node_id=self._id(), kind=EquationNodeKind.OPERAND, symbol=symbol, spoken_form=symbol or "unreadable")

    def _operator(self, symbol: str, children: List[EquationNode]) -> EquationNode:
        spoken = f" {_SPOKEN.get(symbol, symbol)} ".join(child.spoken_form for child in children)
        return EquationNode(node_id=self._id(), kind=EquationNodeKind.OPERATOR, symbol=symbol, spoken_form=spoken, children=children)

    # -- grammar ---------------------------------------------------------
    def parse(self) -> EquationNode:
        left = self._expression()
        token = self._peek()
        if token is not None and token.kind == "op" and token.value == "=":
            self._take()
            right = self._expression()
            node = self._operator("=", [left, right])
        else:
            node = left
        if self._peek() is not None:
            raise MalformedProviderResponseError(EXTRACTION_PROVIDER, "unexpected trailing tokens in equation", field="equation")
        return node

    def _expression(self) -> EquationNode:
        node = self._term()
        while True:
            token = self._peek()
            if token is None or token.kind != "op" or token.value not in ("+", "-"):
                return node
            self._take()
            right = self._term()
            if token.value == "+" and node.kind is EquationNodeKind.OPERATOR and node.symbol == "+":
                node = self._operator("+", [*node.children, right])
            else:
                node = self._operator(token.value, [node, right])

    def _term(self) -> EquationNode:
        node = self._unary()
        while True:
            token = self._peek()
            if token is None:
                return node
            if token.kind == "op" and token.value in ("*", "×", "·", "/", "÷"):
                self._take()
                symbol = _MULTIPLY.get(token.value, token.value)
            elif token.kind == "command" and token.value in ("\\times", "\\cdot"):
                self._take()
                symbol = _MULTIPLY[token.value]
            elif token.kind in ("ident", "number", "unreadable") or (token.kind == "op" and token.value == "(") or (
                token.kind == "command" and token.value == "\\frac"
            ):
                symbol = "×"  # implicit multiplication, e.g. "IR"
            else:
                return node
            right = self._unary()
            if symbol in ("×", "·") and node.kind is EquationNodeKind.OPERATOR and node.symbol == symbol:
                node = self._operator(symbol, [*node.children, right])
            else:
                node = self._operator(symbol, [node, right])

    def _unary(self) -> EquationNode:
        token = self._peek()
        if token is not None and token.kind == "op" and token.value in ("-", "+"):
            self._take()
            operand = self._unary()
            if token.value == "+":
                return operand
            if operand.kind is EquationNodeKind.OPERAND and operand.symbol is not None and _is_number(operand.symbol):
                symbol = operand.symbol[1:] if operand.symbol.startswith("-") else "-" + operand.symbol
                return EquationNode(node_id=operand.node_id, kind=EquationNodeKind.OPERAND, symbol=symbol, spoken_form=f"negative {operand.symbol}")
            return EquationNode(
                node_id=self._id(), kind=EquationNodeKind.FUNCTION, symbol="-", spoken_form=f"negative {operand.spoken_form}", children=[operand]
            )
        return self._power()

    def _power(self) -> EquationNode:
        base = self._atom()
        token = self._peek()
        if token is not None and token.kind == "op" and token.value == "_":
            self._take()
            index = self._subscript_label() or self._atom()
            base = EquationNode(
                node_id=self._id(), kind=EquationNodeKind.SUBSCRIPT, spoken_form=f"{base.spoken_form} sub {index.spoken_form}", children=[base, index]
            )
            token = self._peek()
        if token is not None and token.kind == "op" and token.value == "^":
            self._take()
            exponent = self._unary_atom()
            base = EquationNode(
                node_id=self._id(),
                kind=EquationNodeKind.SUPERSCRIPT,
                spoken_form=f"{base.spoken_form} to the power {exponent.spoken_form}",
                children=[base, exponent],
            )
        return base

    def _subscript_label(self) -> Optional[EquationNode]:
        """'{out}' after '_' is one label, not the product o × u × t."""

        token = self._peek()
        if token is None or token.kind != "op" or token.value != "{":
            return None
        end = self._position + 1
        parts: List[str] = []
        while end < len(self._tokens) and self._tokens[end].kind in ("ident", "number"):
            parts.append(self._tokens[end].value)
            end += 1
        closing = self._tokens[end] if end < len(self._tokens) else None
        if not parts or closing is None or closing.kind != "op" or closing.value != "}":
            return None
        self._position = end + 1
        return self._operand("".join(parts))

    def _unary_atom(self) -> EquationNode:
        token = self._peek()
        if token is not None and token.kind == "op" and token.value == "-":
            return self._unary()
        return self._atom()

    def _atom(self) -> EquationNode:
        token = self._take()
        if token.kind == "number":
            return self._operand(token.value)
        if token.kind == "ident":
            return self._operand(token.value)
        if token.kind == "unreadable":
            return self._operand(None)
        if token.kind == "op" and token.value in ("(", "{"):
            closing = ")" if token.value == "(" else "}"
            inner = self._expression()
            self._expect_op(closing)
            if token.value == "{":
                return inner
            return EquationNode(node_id=self._id(), kind=EquationNodeKind.GROUP, spoken_form=f"open bracket {inner.spoken_form} close bracket", children=[inner])
        if token.kind == "command" and token.value == "\\frac":
            self._expect_op("{")
            numerator = self._expression()
            self._expect_op("}")
            self._expect_op("{")
            denominator = self._expression()
            self._expect_op("}")
            return EquationNode(
                node_id=self._id(),
                kind=EquationNodeKind.FRACTION,
                spoken_form=f"fraction {numerator.spoken_form} over {denominator.spoken_form}",
                children=[numerator, denominator],
            )
        raise MalformedProviderResponseError(EXTRACTION_PROVIDER, "equation has an operator where a value was expected", field="equation")


def _is_number(symbol: str) -> bool:
    try:
        float(symbol)
    except ValueError:
        return False
    return True


def parse_basic_equation(
    text: str,
    *,
    equation_id: UUID,
    reference: EquationEvidenceReference,
    id_prefix: str,
) -> EquationTree:
    """Build a GENERATED EquationTree from parser text; see the module docstring."""

    root = _Parser(_tokenize(text), id_prefix).parse()
    return EquationTree(
        equation_id=equation_id,
        reference=reference,
        root=root,
        extraction_source=ObservationSource.GENERATED,
    )
