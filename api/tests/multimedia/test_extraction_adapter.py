"""Extraction adapter: parser output -> structure -> check against the original.

The parser double returns SYNTHETIC text shaped like LlamaParse Markdown
output for the AgentSpec fixture. The reviewer records come from
fixtures/ohm_law.py, which are synthetic stand-ins for a reviewer's
reading of the original PDF (not an original-media review).
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

import pytest

from fixtures.ohm_law import EQUATION_INDEX, SOURCE_VERSION_ID, TABLE_INDEX, equation_source_check, table_source_check
from fixtures.provider_fakes import local_tracer
from netra_api.multimedia.equations.models import EquationEvidenceReference, EquationNodeKind, EquationTree
from netra_api.multimedia.equations.validation import EquationSourceCheck, canonical_form
from netra_api.multimedia.evidence import ObservationSource
from netra_api.multimedia.extraction.adapter import ParserBackedObjectExtraction, candidate_id
from netra_api.multimedia.extraction.equations import parse_basic_equation
from netra_api.multimedia.extraction.tables import parse_markdown_table, parse_number, split_unit
from netra_api.multimedia.providers.errors import (
    ExtractionUnsupportedError,
    MalformedProviderResponseError,
    ProviderTimeoutError,
)
from netra_api.multimedia.tables.models import TableEvidenceReference, TableStructure
from netra_worker.jobs.multimedia.extraction import (
    ExtractedObjectKind,
    ExtractionOutcome,
    ExtractObjectPayload,
    is_citable,
)
from netra_worker.jobs.multimedia.equations import ExtractEquationJob
from netra_worker.jobs.multimedia.figures import ExtractChartJob
from netra_worker.jobs.multimedia.tables import ExtractTableJob

OHM_TABLE_MD = """| Current (A) | Voltage (V) |
|---|---|
| 1 | 2 |
| 2 | 4 |
| 3 | 6 |"""


@dataclass
class Block:
    text: str
    block_type_hint: str


class Parser:
    def __init__(self, blocks, delay=0.0):
        self.blocks = blocks
        self.delay = delay
        self.calls = []

    async def parse(self, object_key, content_type):
        import asyncio

        self.calls.append((object_key, content_type))
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.blocks


class Checks:
    def __init__(self, table=None, equation=None):
        self.table = table
        self.equation = equation
        self.asked = []

    async def table_check(self, *, source_version_id, table_index):
        self.asked.append(("table", source_version_id, table_index))
        return self.table if source_version_id == SOURCE_VERSION_ID else None

    async def equation_check(self, *, source_version_id, equation_index):
        self.asked.append(("equation", source_version_id, equation_index))
        return self.equation if source_version_id == SOURCE_VERSION_ID else None


def _adapter(blocks, checks, **kwargs):
    return ParserBackedObjectExtraction(Parser(blocks), checks, parser_provider="llamaparse", crop_content_type="image/png", **kwargs)


async def _extract(adapter, kind, index, version=SOURCE_VERSION_ID):
    return await adapter.extract(object_key="crops/obj.png", kind=kind, object_index=index, source_version_id=version, timeout_seconds=5)


# ---------------------------------------------------------------- converters


def _table_ref():
    return TableEvidenceReference(evidence_id="pending:table:1", source_version_id=SOURCE_VERSION_ID, locator="table:1", table_index=1)


def test_markdown_table_keeps_headers_units_and_exact_cells():
    table = parse_markdown_table(OHM_TABLE_MD, table_id=uuid4(), reference=_table_ref(), id_prefix="tbl01")
    assert (table.row_count, table.column_count) == (4, 2)
    assert [(h.text, h.unit) for h in table.headers] == [("Current", "A"), ("Voltage", "V")]
    row = table.find_row_by_value(0, 2.0)
    assert row == 2 and table.cell_at(row, 1).numeric_value == 4.0
    assert [h.text for h in table.headers_for(table.cell_at(row, 1))] == ["Voltage"]


def test_number_parsing_keeps_signs_and_explicit_units_only():
    assert parse_number("−2") == (-2.0, None)
    assert parse_number("2 V") == (2.0, "V")
    assert parse_number("about 2") == (None, None)
    assert split_unit("Resistance [ohm]") == ("Resistance", "ohm")
    assert split_unit("Trial") == ("Trial", None)


def test_ragged_or_html_tables_are_refused_not_guessed():
    with pytest.raises(MalformedProviderResponseError):
        parse_markdown_table("| a | b |\n|---|---|\n| 1 |", table_id=uuid4(), reference=_table_ref(), id_prefix="t")
    with pytest.raises(ExtractionUnsupportedError):
        parse_markdown_table("<table><tr><td colspan=2>x</td></tr></table>", table_id=uuid4(), reference=_table_ref(), id_prefix="t")


def _eq(text):
    reference = EquationEvidenceReference(evidence_id="pending:equation:1", source_version_id=SOURCE_VERSION_ID, locator="equation:1", equation_index=1)
    return parse_basic_equation(text, equation_id=uuid4(), reference=reference, id_prefix="eq01")


@pytest.mark.parametrize(
    "text, canonical",
    [
        ("V = I × R", "(V = (I × R))"),
        ("$V = I \\times R$", "(V = (I × R))"),
        ("V = IR", "(V = (I × R))"),
        ("V = I * R", "(V = (I × R))"),
        ("P = I^2 R", "(P = ((I ^ 2) × R))"),
        ("R = \\frac{V}{I}", "(R = (V / I))"),
        ("a = b - c - d", "(a = ((b - c) - d))"),
        ("a = b + c + d", "(a = (b + c + d))"),
        ("x = -2", "(x = -2)"),
        ("x = -(a + b)", "(x = -(((a + b))))"),
        ("V_{out} = V_{in} − IR", "((V _ out) = ((V _ in) - (I × R)))"),
        ("E = m c^2", "(E = (m × (c ^ 2)))"),
        ("\\Delta V = I \\Delta R", "((Δ × V) = (I × Δ × R))"),
    ],
)
def test_basic_equations_parse_to_the_reviewer_comparable_canonical_form(text, canonical):
    tree = _eq(text)
    assert canonical_form(tree.root) == canonical
    assert tree.extraction_source is ObservationSource.GENERATED


def test_a_flipped_operator_or_sign_stays_visible_in_the_canonical_form():
    assert canonical_form(_eq("V = I ÷ R").root) != "(V = (I × R))"
    assert canonical_form(_eq("x = 2").root) != canonical_form(_eq("x = -2").root)


def test_unreadable_marker_is_an_honest_hole():
    tree = _eq("V = I × ?")
    holes = [node for node in [tree.root, *tree.root.children, *tree.root.children[1].children] if node.symbol is None and node.kind is EquationNodeKind.OPERAND]
    assert len(holes) == 1 and holes[0].spoken_form == "unreadable"


@pytest.mark.parametrize("text", ["\\int_0^1 x dx = 1/2", "\\sum x = 3", "\\begin{matrix} 1 \\end{matrix}"])
def test_beyond_basic_equations_are_unsupported(text):
    with pytest.raises(ExtractionUnsupportedError):
        _eq(text)


@pytest.mark.parametrize("text", ["V = = R", "V = (I × R", "", "V = I §"])
def test_malformed_equations_are_rejected(text):
    with pytest.raises(MalformedProviderResponseError):
        _eq(text)


# ---------------------------------------------------------------- adapter


async def test_table_extraction_verified_against_the_reviewer_record():
    checks = Checks(table=table_source_check())
    outcome = await _extract(_adapter([Block("Figure caption", "paragraph"), Block(OHM_TABLE_MD, "table")], checks), "table", TABLE_INDEX)

    assert outcome["validation"]["source_verified"] is True
    assert outcome["validation"]["mismatch_count"] == 0
    structure = TableStructure.model_validate(outcome["structure"])
    assert structure.table_id == candidate_id(SOURCE_VERSION_ID, "table", TABLE_INDEX)
    assert structure.reference.evidence_id.startswith("pending:")
    record = ExtractionOutcome.model_validate(outcome)
    assert is_citable(record)


async def test_wrong_unit_is_a_mismatch_and_never_citable():
    wrong = OHM_TABLE_MD.replace("Voltage (V)", "Voltage (mV)")
    outcome = await _extract(_adapter([Block(wrong, "table")], Checks(table=table_source_check())), "table", TABLE_INDEX)
    assert outcome["validation"]["source_verified"] is False
    assert outcome["validation"]["mismatch_count"] == 1
    assert any(f["field"] == "unit" and f["status"] == "mismatch" for f in outcome["findings"])
    assert not is_citable(ExtractionOutcome.model_validate(outcome))


async def test_wrong_cell_value_is_caught_cell_by_cell():
    wrong = OHM_TABLE_MD.replace("| 2 | 4 |", "| 2 | 5 |")
    outcome = await _extract(_adapter([Block(wrong, "table")], Checks(table=table_source_check())), "table", TABLE_INDEX)
    assert not outcome["validation"]["source_verified"]
    assert any(f["part_id"] == "tbl01.r2.c1" and f["status"] == "mismatch" for f in outcome["findings"])


async def test_without_a_reviewer_record_extraction_is_stored_but_not_checked():
    outcome = await _extract(_adapter([Block(OHM_TABLE_MD, "table")], Checks()), "table", TABLE_INDEX)
    assert outcome["validation"] == {**outcome["validation"], "source_verified": False, "checked_count": 0}
    assert not is_citable(ExtractionOutcome.model_validate(outcome))


async def test_a_record_for_another_source_version_is_not_a_check():
    outcome = await _extract(_adapter([Block(OHM_TABLE_MD, "table")], Checks(table=table_source_check())), "table", TABLE_INDEX, version=uuid4())
    assert outcome["validation"]["checked_count"] == 0


async def test_equation_structure_verifies_but_missing_units_block_citation():
    no_units = equation_source_check().model_copy(update={"units_by_symbol": {}})
    verified = await _extract(_adapter([Block("V = I \\times R", "equation")], Checks(equation=no_units)), "equation", EQUATION_INDEX)
    assert verified["validation"]["source_verified"] is True
    assert EquationTree.model_validate(verified["structure"]).extraction_source is ObservationSource.OBSERVED

    with_units = await _extract(_adapter([Block("V = I \\times R", "equation")], Checks(equation=equation_source_check())), "equation", EQUATION_INDEX)
    assert with_units["validation"]["source_verified"] is False
    assert with_units["validation"]["mismatch_count"] == 3  # V, I and R lost their units
    assert EquationTree.model_validate(with_units["structure"]).extraction_source is ObservationSource.GENERATED


async def test_sign_error_in_equation_is_a_mismatch():
    check = EquationSourceCheck(equation_index=EQUATION_INDEX, canonical_form="(V = (I × R))")
    outcome = await _extract(_adapter([Block("V = I ÷ R", "equation")], Checks(equation=check)), "equation", EQUATION_INDEX)
    assert not outcome["validation"]["source_verified"]


async def test_unreadable_in_source_is_a_stated_limitation_not_a_pass():
    check = EquationSourceCheck(equation_index=EQUATION_INDEX, canonical_form="(V = (I × R))", unreadable_in_source=True)
    outcome = await _extract(_adapter([Block("V = I × R", "equation")], Checks(equation=check)), "equation", EQUATION_INDEX)
    assert outcome["validation"]["source_verified"] is False and outcome["validation"]["unreadable_count"] >= 1


@pytest.mark.parametrize("kind", ["chart", "figure", "diagram"])
async def test_visual_structure_kinds_are_explicitly_unsupported(kind):
    parser = Parser([Block("x axis: Current", "text")])
    adapter = ParserBackedObjectExtraction(parser, Checks(), parser_provider="llamaparse", crop_content_type="image/png")
    with pytest.raises(ExtractionUnsupportedError):
        await _extract(adapter, kind, 2)
    assert parser.calls == []


async def test_ambiguous_or_empty_crops_are_refused():
    with pytest.raises(MalformedProviderResponseError):
        await _extract(_adapter([Block(OHM_TABLE_MD, "table"), Block(OHM_TABLE_MD, "table")], Checks()), "table", TABLE_INDEX)
    with pytest.raises(MalformedProviderResponseError):
        await _extract(_adapter([Block("just prose", "paragraph")], Checks()), "table", TABLE_INDEX)


async def test_parser_timeout_is_a_netra_timeout():
    adapter = ParserBackedObjectExtraction(Parser([Block(OHM_TABLE_MD, "table")], delay=1), Checks(), parser_provider="llamaparse", crop_content_type="image/png")
    with pytest.raises(ProviderTimeoutError):
        await adapter.extract(object_key="k", kind="table", object_index=1, source_version_id=SOURCE_VERSION_ID, timeout_seconds=0.01)


async def test_repeated_extraction_preserves_ids():
    adapter = _adapter([Block(OHM_TABLE_MD, "table")], Checks(table=table_source_check()))
    first = await _extract(adapter, "table", TABLE_INDEX)
    second = await _extract(adapter, "table", TABLE_INDEX)
    assert first["structure"] == second["structure"]


async def test_extraction_span_records_uncertainty_and_no_content():
    tracer, exporter = local_tracer()
    await _extract(_adapter([Block(OHM_TABLE_MD, "table")], Checks(), tracer=tracer), "table", TABLE_INDEX)
    tracer.shutdown(1)
    parent = next(span for span in exporter.spans if span.name == "media.extract")
    assert parent.attributes["netra.outcome"] == "unverified"
    assert parent.attributes["netra.gap"] == "not_checked"
    assert "Current" not in repr(exporter.spans) and "crops/obj.png" not in repr(exporter.spans)


# ---------------------------------------------------------------- worker jobs


class Recorder:
    def __init__(self):
        self.stages = []

    async def completed_stages(self):
        return list(self.stages)

    async def remote_operation_id(self, stage):
        return None

    async def record(self, stage, remote_operation_id=None):
        self.stages.append(stage)


class Sink:
    def __init__(self):
        self.stored = []

    async def store_candidate(self, *, source_version_id, outcome, citable, idempotency_key):
        self.stored.append((outcome, citable, idempotency_key))


def _payload(index):
    return ExtractObjectPayload(idempotency_key="x-1", source_id=uuid4(), source_version_id=SOURCE_VERSION_ID, object_index=index, object_key="crops/obj.png")


async def test_table_job_with_the_real_adapter_stores_a_citable_verified_candidate():
    sink = Sink()
    await ExtractTableJob(_adapter([Block(OHM_TABLE_MD, "table")], Checks(table=table_source_check())), sink, Recorder()).handle(_payload(TABLE_INDEX))
    (outcome, citable, key), = sink.stored
    assert citable and key == "x-1" and outcome.kind is ExtractedObjectKind.TABLE


async def test_equation_job_with_missing_units_stores_but_does_not_cite():
    sink = Sink()
    await ExtractEquationJob(_adapter([Block("V = I × R", "equation")], Checks(equation=equation_source_check())), sink, Recorder()).handle(_payload(EQUATION_INDEX))
    (outcome, citable, _), = sink.stored
    assert not citable and outcome.validation.mismatch_count == 3


async def test_chart_job_with_the_real_adapter_fails_explicitly_and_stores_nothing():
    sink, recorder = Sink(), Recorder()
    with pytest.raises(ExtractionUnsupportedError):
        await ExtractChartJob(_adapter([], Checks()), sink, recorder).handle(_payload(2))
    assert sink.stored == [] and recorder.stages == []
