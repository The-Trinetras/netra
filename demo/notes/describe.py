"""Read tables and equations aloud: deterministic, no model, no provider.

Ported (the part that needs no vision or video provider) from the earlier project's multimedia
work, which existed so that a blind or low-vision student gets tables and equations as usable
language and not as pipe characters and symbols. A screen reader on `| 2 | 1 | 2 |` says noise;
"Row 1: Voltage is 2, Current is 1" is usable.

How it is used: ingestion appends a "[Read aloud: ...]" paragraph after each table and after each
paragraph with equations. The ORIGINAL text is left exactly as written, so an answer can still quote
the notes verbatim and the grounding checks still hold; the read-aloud text is extra, retrievable
material beside it.

Deliberately simple and bounded. It reads what is on the page: it does not evaluate, simplify or
correct an equation, and anything it does not recognise is left alone rather than guessed at.

NOT ported: figures, diagrams and video. Those need a vision or video provider (and API keys) and
were the bulk of the earlier project's multimedia code. They are not done.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

MAX_TABLE_CELLS = 400
MAX_EQUATIONS = 40

_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_EQUATION = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\^\w+)?\s*=\s*[^.;\n]*[A-Za-z0-9)]")


@dataclass(frozen=True)
class Table:
    header: list[str]
    rows: list[list[str]]
    first_line: int
    last_line: int


def _cells(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def find_tables(text: str) -> list[Table]:
    """GitHub-style pipe tables: a header row, a separator row, then body rows. Anything else that
    merely contains a pipe is not a table."""
    lines = text.split("\n")
    tables, i = [], 0
    while i < len(lines) - 1:
        if "|" in lines[i] and _SEPARATOR.match(lines[i + 1]) and "-" in lines[i + 1]:
            header = _cells(lines[i])
            j, rows = i + 2, []
            while j < len(lines) and "|" in lines[j] and lines[j].strip():
                rows.append(_cells(lines[j]))
                j += 1
            if rows and len(header) >= 2:
                tables.append(Table(header, rows, i, j - 1))
            i = j
        else:
            i += 1
    return tables


def table_to_speech(table: Table) -> str:
    """"Table with 3 columns and 2 rows. Columns: A, B, C. Row 1: A is 1, B is 2, C is 3." """
    if len(table.header) * len(table.rows) > MAX_TABLE_CELLS:
        return (f"Table with {len(table.header)} columns and {len(table.rows)} rows. "
                f"Columns: {', '.join(table.header)}. It is too large to read out in full.")
    parts = [f"Table with {len(table.header)} columns and {len(table.rows)} "
             f"row{'s' if len(table.rows) != 1 else ''}.", f"Columns: {', '.join(table.header)}."]
    for number, row in enumerate(table.rows, start=1):
        cells = [f"{name} is {row[k] if k < len(row) and row[k] else 'empty'}"
                 for k, name in enumerate(table.header)]
        parts.append(f"Row {number}: {', '.join(cells)}.")
    return " ".join(parts)


def equation_to_speech(expression: str) -> str:
    """"V = I x R" -> "V equals I times R". Reads symbols; never changes or checks the mathematics."""
    s = re.sub(r"\+\s*\.\.\.", " and so on", expression.strip())   # before the trailing dots are trimmed
    s = s.rstrip(".;")
    s = s.replace("Ω", " ohms").replace("Ω", " ohms")
    s = re.sub(r"\^2\b", " squared", s)
    s = re.sub(r"\^3\b", " cubed", s)
    s = re.sub(r"\^(\w+)", r" to the power of \1", s)
    s = re.sub(r"_(\w+)", r" sub \1", s)
    s = s.replace("×", " times ").replace("*", " times ")
    # A lone "x" is multiplication only BETWEEN two operands ("I x R"); after "=" or an operator it is
    # a variable ("y = x^3").
    s = re.sub(r"(?<=[A-Za-z0-9)])\s+x\s+(?=[A-Za-z0-9(])", " times ", s)
    s = s.replace("÷", " divided by ").replace("/", " divided by ")
    s = s.replace("+", " plus ")
    s = s.replace("−", " minus ")
    s = re.sub(r"(?<=\s)-(?=\s)", " minus ", s)
    s = re.sub(r"(?<![\w)])-(?=\d)", "minus ", s)
    s = s.replace("=", " equals ")
    return re.sub(r"\s+", " ", s).strip()


def find_equations(paragraph: str) -> list[str]:
    return [m.group(0).strip() for m in _EQUATION.finditer(paragraph)]


_ANNOTATION = re.compile(r"\n\n\[(?:Table read aloud|Read aloud): [^\]\n]*\]")


def strip_annotations(text: str) -> str:
    """Remove read-aloud paragraphs this module added, so annotating twice gives the same result."""
    return _ANNOTATION.sub("", text)


def _bracketless(text: str) -> str:
    """Square brackets delimit an annotation, so none may come from the content inside it."""
    return text.replace("[", "(").replace("]", ")")


def annotate(text: str) -> str:
    """The text with a read-aloud paragraph after each table and after each paragraph that has
    equations. The original text is untouched, and the result is idempotent: annotating already
    annotated text gives the same text."""
    text = strip_annotations(text)
    lines = text.split("\n")
    tables = find_tables(text)
    after_line = {t.last_line: t for t in tables}
    in_table = {n for t in tables for n in range(t.first_line, t.last_line + 1)}

    out: list[str] = []
    paragraph: list[str] = []
    equations_left = MAX_EQUATIONS

    def flush() -> None:
        nonlocal equations_left
        if not paragraph:
            return
        found = find_equations(" ".join(paragraph))[:equations_left]
        equations_left -= len(found)
        if found:
            out.append("")
            out.append("[Read aloud: " + _bracketless(" ".join(equation_to_speech(e) + "." for e in found)) + "]")
        paragraph.clear()

    for number, line in enumerate(lines):
        out.append(line)
        if number in in_table:
            if number in after_line:
                out.append("")
                out.append("[Table read aloud: " + _bracketless(table_to_speech(after_line[number])) + "]")
            continue
        if line.strip():
            paragraph.append(line)
        else:
            flush()
    flush()
    return "\n".join(out)
