"""C6 evidence view: schema <-> mirror, provenance/AI flag and examples from real fixtures.

Run with EVIDENCE_VIEW_WRITE_EXAMPLES=1 to regenerate the contract examples
from the builder (then review the diff); normally the test only compares.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from fixtures.ohm_law import (
    SOURCE_VERSION_ID,
    chart_source_check,
    equation_source_check,
    extracted_chart,
    extracted_equation,
    extracted_table,
    table_source_check,
)
from netra_api.multimedia.equations.validation import validate_equation_against_source
from netra_api.multimedia.evidence import ObservationSource
from netra_api.multimedia.evidence_view import (
    EvidenceView,
    ObjectKind,
    Provenance,
    ProvenanceOrigin,
    SourceCheck,
    build_evidence_view,
    source_check_for,
)
from netra_api.multimedia.exploration import ExplorationLayer, explore_chart, explore_equation, explore_table
from netra_api.multimedia.figures.validation import validate_chart_against_source
from netra_api.multimedia.tables.validation import validate_table_against_source
from netra_api.multimedia.validation import ValidationReport

REPO = Path(__file__).resolve().parents[3]
SCHEMA = json.loads((REPO / "shared" / "contracts" / "protocol" / "v1" / "evidence_view.schema.json")
                    .read_text(encoding="utf-8"))
EXAMPLES = REPO / "shared" / "contracts" / "examples" / "server"
EXTRACTION = Provenance(origin=ProvenanceOrigin.EXTRACTION, provider="fixture-extractor")


def _table_view(layer=ExplorationLayer.PARTS, part_id=None, **table_kwargs):
    table = extracted_table(**table_kwargs)
    return build_evidence_view(
        explore_table(table, layer, part_id), evidence_id="ev-ohm-table", source_version_id=SOURCE_VERSION_ID,
        evidence_version=2, object_kind=ObjectKind.TABLE, locator="chapter 4, table 4.1", provenance=EXTRACTION,
        report=validate_table_against_source(table, table_source_check()), table=table, part_id=part_id)


def _chart_view(layer=ExplorationLayer.RELATIONSHIPS):
    chart = extracted_chart()
    return build_evidence_view(
        explore_chart(chart, layer), evidence_id="ev-ohm-graph", source_version_id=SOURCE_VERSION_ID,
        evidence_version=2, object_kind=ObjectKind.CHART, locator="chapter 4, figure 4.2", provenance=EXTRACTION,
        report=validate_chart_against_source(chart, chart_source_check()))


def _equation_view(layer=ExplorationLayer.OVERVIEW):
    tree = extracted_equation()
    return build_evidence_view(
        explore_equation(tree, layer), evidence_id="ev-ohm-equation", source_version_id=SOURCE_VERSION_ID,
        evidence_version=2, object_kind=ObjectKind.EQUATION, locator="chapter 4, equation 4.1",
        provenance=EXTRACTION, report=validate_equation_against_source(tree, equation_source_check()),
        equation=tree)


BUILDERS = {"evidence_view_table": _table_view, "evidence_view_chart": _chart_view,
            "evidence_view_equation": _equation_view}


def _schema_enum(*path):
    node = SCHEMA
    for key in path:
        node = node[key]
    return set(node["enum"])


def test_schema_enums_match_the_mirror():
    assert _schema_enum("properties", "object_kind") == {k.value for k in ObjectKind}
    assert _schema_enum("properties", "layer") == {layer.value for layer in ExplorationLayer}
    assert _schema_enum("properties", "source_check") == {c.value for c in SourceCheck}
    assert _schema_enum("$defs", "LabelSource") == {s.value for s in ObservationSource}
    assert _schema_enum("$defs", "Provenance", "properties", "origin") == {o.value for o in ProvenanceOrigin}


def test_schema_fields_and_required_match_the_mirror():
    assert set(SCHEMA["properties"]) == set(EvidenceView.model_fields)
    required = {name for name, field in EvidenceView.model_fields.items() if field.is_required()}
    assert set(SCHEMA["required"]) == required


@pytest.mark.parametrize("name", sorted(BUILDERS))
def test_examples_are_exactly_what_the_builder_produces_from_real_fixtures(name):
    produced = {"view": BUILDERS[name]().public()}
    path = EXAMPLES / f"{name}.json"
    if os.environ.get("EVIDENCE_VIEW_WRITE_EXAMPLES") == "1":
        path.write_text(json.dumps(produced, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    example = json.loads(path.read_text(encoding="utf-8"))
    assert EvidenceView.model_validate(example["view"]).public() == example["view"]
    assert produced == example


def test_table_view_carries_headers_cells_and_governing_headers():
    view = _table_view()
    grid = view.table
    assert (grid.row_count, grid.column_count) == (extracted_table().row_count, extracted_table().column_count)
    assert grid.headers and grid.cells
    assert all(cell.header_ids for cell in grid.cells if cell.row_index > 0)
    assert any(cell.unit for cell in grid.cells)  # unit inherited from the header


def test_overview_layer_omits_the_grid():
    assert _table_view(ExplorationLayer.OVERVIEW).table is None


def test_verified_extraction_reports_verified_and_no_ai_flag():
    view = _table_view()
    assert view.source_check is SourceCheck.VERIFIED
    assert view.ai_description is False


def test_a_verified_equation_is_observed_and_not_an_ai_description():
    view = _equation_view()
    assert view.source_check is SourceCheck.VERIFIED
    assert view.equation.extraction_source is ObservationSource.OBSERVED and view.ai_description is False


def test_an_unchecked_equation_stays_generated_and_is_flagged():
    tree = extracted_equation()
    view = build_evidence_view(
        explore_equation(tree, ExplorationLayer.OVERVIEW), evidence_id="ev-eq", source_version_id=SOURCE_VERSION_ID,
        evidence_version=1, object_kind=ObjectKind.EQUATION, locator="equation 4.1", provenance=EXTRACTION,
        equation=tree)
    assert view.source_check is SourceCheck.NOT_CHECKED
    assert view.equation.extraction_source is ObservationSource.GENERATED and view.ai_description is True


def test_mismatched_table_is_reported_as_mismatch():
    table = extracted_table(voltage_unit="mV")
    report = validate_table_against_source(table, table_source_check())
    assert source_check_for(report) is SourceCheck.MISMATCH


@pytest.mark.parametrize("report,expected", [
    (None, SourceCheck.NOT_CHECKED),
    (ValidationReport(object_id="empty"), SourceCheck.NOT_CHECKED),  # nothing checked is never a pass
])
def test_absent_or_empty_reports_are_not_checked(report, expected):
    assert source_check_for(report) is expected


def test_generated_labels_force_the_ai_flag_and_a_false_flag_is_refused():
    view = _chart_view(ExplorationLayer.PARTS).model_dump()
    labelled = next(i for i, part in enumerate(view["parts"]) if part.get("label"))
    view["parts"][labelled]["label_source"] = "generated"
    generated = view
    with pytest.raises(ValidationError, match="ai_description"):
        EvidenceView.model_validate({**generated, "ai_description": False})
    assert EvidenceView.model_validate({**generated, "ai_description": True}).ai_description is True


@pytest.mark.parametrize("change", [
    {"layer": "detail"},    # detail requires part_id
    {"part_id": "p1"},      # part_id only at the detail layer
])
def test_layer_and_part_rules(change):
    with pytest.raises(ValidationError):
        EvidenceView.model_validate({**_chart_view().model_dump(), **change})


def test_only_a_table_carries_a_grid():
    grid = _table_view().model_dump()["table"]
    with pytest.raises(ValidationError, match="table grid"):
        EvidenceView.model_validate({**_chart_view().model_dump(), "table": grid})


def test_an_equation_view_needs_equation_forms_and_others_must_not_have_them():
    equation = _equation_view().model_dump()
    with pytest.raises(ValidationError):
        EvidenceView.model_validate({**equation, "equation": None})
    chart = _chart_view().model_dump()
    with pytest.raises(ValidationError):
        EvidenceView.model_validate({**chart, "equation": equation["equation"]})


def test_views_are_deterministic_for_the_same_structure():
    assert _table_view().public() == _table_view().public()
