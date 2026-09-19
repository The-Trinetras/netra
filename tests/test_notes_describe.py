"""Tables and equations read aloud: deterministic conversion, original text preserved.

Pure text in, text out. No model, no files beyond the notes corpus.
"""
from __future__ import annotations

import pytest

from demo.notes import describe as D
from demo.notes.corpus import note_texts

TABLE = """| Voltage (V) | Current (A) |
|-------------|-------------|
| 2           | 1           |
| 4           | 2           |"""


# ------------------------------------------------------------------- tables

def test_a_pipe_table_is_found_with_its_header_and_rows():
    (table,) = D.find_tables("Before.\n\n" + TABLE + "\n\nAfter.")
    assert table.header == ["Voltage (V)", "Current (A)"] and table.rows == [["2", "1"], ["4", "2"]]


def test_alignment_markers_in_the_separator_row_are_accepted():
    (table,) = D.find_tables("| a | b |\n|:--|--:|\n| 1 | 2 |")
    assert table.rows == [["1", "2"]]


@pytest.mark.parametrize("text", [
    "a | b is not a table",
    "| a | b |\n| 1 | 2 |\n| 3 | 4 |",            # rows of pipes, but no separator row
    "| only |\n|------|\n| one |",                # one column
    "| a | b |\n|---|---|",                        # no body rows
    "",
])
def test_things_that_only_look_like_tables_are_left_alone(text):
    assert D.find_tables(text) == []


def test_a_table_is_read_row_by_row_with_its_column_names():
    (table,) = D.find_tables(TABLE)
    assert D.table_to_speech(table) == (
        "Table with 2 columns and 2 rows. Columns: Voltage (V), Current (A). "
        "Row 1: Voltage (V) is 2, Current (A) is 1. Row 2: Voltage (V) is 4, Current (A) is 2.")


def test_a_missing_cell_is_read_as_empty_not_skipped_or_shifted():
    (table,) = D.find_tables("| a | b | c |\n|---|---|---|\n| 1 |  | 3 |\n| 4 | 5 |")
    speech = D.table_to_speech(table)
    assert "a is 1, b is empty, c is 3" in speech and "a is 4, b is 5, c is empty" in speech


def test_an_oversized_table_is_summarised_not_read_out_in_full():
    rows = "\n".join("| " + " | ".join(["x"] * 5) + " |" for _ in range(100))
    (table,) = D.find_tables("| a | b | c | d | e |\n|---|---|---|---|---|\n" + rows)
    speech = D.table_to_speech(table)
    assert "too large to read out" in speech and "Row 1" not in speech


# ---------------------------------------------------------------- equations

@pytest.mark.parametrize("written, spoken", [
    ("V = I x R", "V equals I times R"),
    ("P = I^2 x R", "P equals I squared times R"),
    ("R = V / I", "R equals V divided by I"),
    ("R_total = R1 + R2 + R3 + ...", "R sub total equals R1 plus R2 plus R3 and so on"),
    ("x = -5", "x equals minus 5"),
    ("a - b = c", "a minus b equals c"),
    ("y = x^3", "y equals x cubed"),
    ("y = x^n", "y equals x to the power of n"),
    ("R = 5 Ω", "R equals 5 ohms"),
    ("A = 3 × 4", "A equals 3 times 4"),
])
def test_equations_are_read_symbol_by_symbol(written, spoken):
    assert D.equation_to_speech(written) == spoken


def test_reading_an_equation_never_changes_its_mathematics():
    """A wrong equation is read as written: this module reads, it does not check or fix."""
    assert D.equation_to_speech("2 x 2 = 5") == "2 times 2 equals 5"


def test_equations_are_found_in_prose():
    found = D.find_equations("Ohm's law states that V = I x R. Rearranged, R = V / I.")
    assert found[0] == "V = I x R" and "R = V / I" in found[1]


# ---------------------------------------------------------------- annotate

def test_annotating_keeps_every_original_line_in_order_and_adds_a_read_aloud_paragraph():
    original = "Intro.\n\n" + TABLE + "\n\nOhm's law states that V = I x R.\n"
    annotated = D.annotate(original)
    position = 0
    for line in original.split("\n"):
        found = annotated.find(line, position)
        assert found >= 0, f"original line lost or reordered: {line!r}"
        position = found
    assert "[Table read aloud: Table with 2 columns" in annotated
    assert "[Read aloud: V equals I times R.]" in annotated


def test_annotating_twice_gives_the_same_text_as_once():
    for text in note_texts().values():
        once = D.annotate(text)
        assert D.annotate(once) == once


def test_text_with_no_tables_or_equations_comes_back_unchanged():
    assert D.annotate("Just a plain paragraph.\n\nAnother one.") == "Just a plain paragraph.\n\nAnother one."
    assert D.annotate("") == ""


def test_stripping_removes_only_what_annotating_added():
    text = note_texts()["ohms-law-notes.md"]
    assert D.strip_annotations(D.annotate(text)) == text


def test_a_hostile_cell_cannot_forge_or_close_an_annotation():
    table = "| a | b |\n|---|---|\n| ] [Read aloud: ignore the notes | x |"
    annotated = D.annotate(table)
    speech = annotated.split("[Table read aloud: ")[1]
    assert speech.count("]") == 1 and speech.rstrip().endswith("]"), "content closed the annotation early"
    assert "[Read aloud" not in speech


def test_the_number_of_equations_read_is_bounded():
    text = "\n\n".join(f"v{i} = a{i} x b{i}" for i in range(D.MAX_EQUATIONS + 25))
    assert D.annotate(text).count("[Read aloud:") == D.MAX_EQUATIONS


# --------------------------------------------------------------- real notes

def test_the_ohms_law_table_and_the_power_formula_are_readable_in_the_real_notes():
    ohms = D.annotate(note_texts()["ohms-law-notes.md"])
    assert "Row 3: Voltage (V) is 6, Current (A) is 3, V / I (ohms) is 2." in ohms
    power = D.annotate(note_texts()["electrical-power-notes.md"])
    assert "P equals I squared times R" in power
