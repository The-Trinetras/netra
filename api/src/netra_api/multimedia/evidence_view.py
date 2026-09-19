"""Public evidence view (C6, shared/contracts/protocol/v1/evidence_view.schema.json).

The accessible shape the client receives for one layer of one figure, chart,
diagram, table or equation. It is built from M3's deterministic
``ExplorationView`` plus the stored structure and its validation report; no
model is called here, and nothing is re-labelled per request.

Two facts are carried explicitly so the client never has to infer them:

- ``source_check`` — whether the extraction was compared with the original
  media and matched (``verified``), contradicted it, was partly unreadable, or
  was never checked. Only ``verified`` content may be presented as the
  document's own words.
- ``ai_description`` — whether any text in the view was produced by a model
  (decision M1-M3-V: shown labelled as an AI description, never as verified
  evidence and never used to grade an answer).
"""

from __future__ import annotations

from enum import Enum
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from netra_api.multimedia.equations.models import EquationTree
from netra_api.multimedia.equations.validation import source_verified_tree
from netra_api.multimedia.evidence import ObservationSource
from netra_api.multimedia.exploration import ExplorationLayer, ExplorationView
from netra_api.multimedia.tables.models import TableStructure
from netra_api.multimedia.tables.validation import resolve_cell_unit
from netra_api.multimedia.validation import ValidationReport


class ObjectKind(str, Enum):
    FIGURE = "figure"
    CHART = "chart"
    DIAGRAM = "diagram"
    TABLE = "table"
    EQUATION = "equation"


class SourceCheck(str, Enum):
    VERIFIED = "verified"
    NOT_CHECKED = "not_checked"
    MISMATCH = "mismatch"
    PARTLY_UNREADABLE = "partly_unreadable"


class ProvenanceOrigin(str, Enum):
    DOCUMENT_STRUCTURE = "document_structure"
    EXTRACTION = "extraction"
    MODEL_DESCRIPTION = "model_description"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SourcedText(_Strict):
    text: str = Field(max_length=2000)
    source: ObservationSource


class Part(_Strict):
    part_id: str = Field(min_length=1, max_length=200)
    label: Optional[str] = Field(default=None, max_length=500)
    label_source: ObservationSource
    detail: Optional[str] = Field(default=None, max_length=2000)
    detail_source: Optional[ObservationSource] = None


class Relationship(_Strict):
    source_part_id: str
    target_part_id: str
    label: Optional[str] = Field(default=None, max_length=500)
    label_source: ObservationSource
    observation_source: ObservationSource
    directed: bool


class TableHeaderView(_Strict):
    header_id: str
    orientation: str = Field(pattern="^(column|row)$")
    index: int = Field(ge=0)
    span: int = Field(ge=1)
    text: Optional[str] = Field(default=None, max_length=500)
    text_source: ObservationSource
    unit: Optional[str] = Field(default=None, max_length=50)
    unit_source: Optional[ObservationSource] = None


class TableCellView(_Strict):
    cell_id: str
    row_index: int = Field(ge=0)
    column_index: int = Field(ge=0)
    row_span: int = Field(ge=1)
    column_span: int = Field(ge=1)
    text: Optional[str] = Field(default=None, max_length=1000)
    text_source: ObservationSource
    unit: Optional[str] = Field(default=None, max_length=50)
    header_ids: List[str]


class TableGrid(_Strict):
    caption: Optional[SourcedText] = None
    row_count: int = Field(ge=0)
    column_count: int = Field(ge=0)
    headers: List[TableHeaderView]
    cells: List[TableCellView] = Field(max_length=5000)


class EquationForms(_Strict):
    spoken_form: str = Field(max_length=2000)
    mathml: Optional[str] = Field(default=None, max_length=20000)
    extraction_source: ObservationSource


class Provenance(_Strict):
    origin: ProvenanceOrigin
    provider: Optional[str] = Field(default=None, max_length=100)
    model_name: Optional[str] = Field(default=None, max_length=200)
    model_version: Optional[str] = Field(default=None, max_length=100)


class EvidenceView(_Strict):
    """Mirror of evidence_view.schema.json, including its conditional rules."""

    evidence_id: str = Field(min_length=1, max_length=200)
    source_version_id: UUID
    evidence_version: int = Field(ge=1)
    object_kind: ObjectKind
    locator: str = Field(min_length=1, max_length=500)
    layer: ExplorationLayer
    part_id: Optional[str] = None
    summary: Optional[SourcedText] = None
    parts: List[Part] = Field(max_length=500)
    relationships: List[Relationship] = Field(max_length=1000)
    unreadable_part_ids: List[str]
    table: Optional[TableGrid] = None
    equation: Optional[EquationForms] = None
    source_check: SourceCheck
    ai_description: bool
    provenance: Provenance

    @model_validator(mode="after")
    def _rules(self) -> "EvidenceView":
        if (self.layer is ExplorationLayer.DETAIL) != (self.part_id is not None):
            raise ValueError("part_id is present if and only if the layer is detail")
        if (self.object_kind is ObjectKind.EQUATION) != (self.equation is not None):
            raise ValueError("equation forms are present if and only if the object is an equation")
        if self.table is not None and self.object_kind is not ObjectKind.TABLE:
            raise ValueError("only a table carries a table grid")
        if self.ai_description != _any_generated(self):
            raise ValueError("ai_description must reflect whether any text is generated")
        return self

    def public(self) -> dict:
        return self.model_dump(mode="json", exclude_none=True)


def _any_generated(view: EvidenceView) -> bool:
    generated = ObservationSource.GENERATED
    sources: list[Optional[ObservationSource]] = [view.summary.source if view.summary else None]
    for part in view.parts:
        sources += [part.label_source if part.label is not None else None, part.detail_source]
    for relationship in view.relationships:
        sources.append(relationship.label_source if relationship.label is not None else None)
    if view.table is not None:
        sources.append(view.table.caption.source if view.table.caption else None)
        sources += [h.text_source for h in view.table.headers if h.text is not None]
        sources += [c.text_source for c in view.table.cells if c.text is not None]
    if view.equation is not None:
        sources.append(view.equation.extraction_source)
    return any(source is generated for source in sources)


def source_check_for(report: Optional[ValidationReport]) -> SourceCheck:
    """Collapse M3's validation report into the public verdict (absent = not checked)."""

    if report is None:
        return SourceCheck.NOT_CHECKED
    if report.is_source_verified:
        return SourceCheck.VERIFIED
    if report.mismatches:
        return SourceCheck.MISMATCH
    if report.unreadable:
        return SourceCheck.PARTLY_UNREADABLE
    return SourceCheck.NOT_CHECKED


def _table_grid(table: TableStructure) -> TableGrid:
    headers = [TableHeaderView(header_id=h.header_id, orientation=h.orientation.value, index=h.index, span=h.span,
                               text=h.text, text_source=h.text_source, unit=h.unit,
                               unit_source=h.unit_source if h.unit is not None else None)
               for h in table.headers]
    cells = [TableCellView(cell_id=c.cell_id, row_index=c.row_index, column_index=c.column_index,
                           row_span=c.row_span, column_span=c.column_span, text=c.text, text_source=c.text_source,
                           unit=resolve_cell_unit(table, c.row_index, c.column_index),
                           header_ids=[h.header_id for h in table.headers_for(c)])
             for c in table.cells]
    caption = SourcedText(text=table.caption, source=table.caption_source) if table.caption else None
    return TableGrid(caption=caption, row_count=table.row_count, column_count=table.column_count,
                     headers=headers, cells=cells)


def build_evidence_view(
    exploration: ExplorationView,
    *,
    evidence_id: str,
    source_version_id: UUID,
    evidence_version: int,
    object_kind: ObjectKind,
    locator: str,
    provenance: Provenance,
    report: Optional[ValidationReport] = None,
    table: Optional[TableStructure] = None,
    equation: Optional[EquationTree] = None,
    part_id: Optional[str] = None,
) -> EvidenceView:
    """Compose the public view for one exploration layer.

    The summary is Netra's own wording assembled from the structure's labels,
    so it inherits their provenance: it counts as generated when any label it
    could draw on (or the equation extraction itself) is generated.
    """

    parts = [Part(part_id=p.part_id, label=p.label, label_source=p.label_source, detail=p.detail,
                  detail_source=p.label_source if p.detail is not None else None)
             for p in exploration.parts]
    relationships = [Relationship(source_part_id=r.source_part_id, target_part_id=r.target_part_id, label=r.label,
                                  label_source=r.label_source, observation_source=r.observation_source,
                                  directed=r.directed)
                     for r in exploration.relationships]
    grid = _table_grid(table) if table is not None and exploration.layer is not ExplorationLayer.OVERVIEW else None
    forms = None
    if equation is not None and report is not None:
        # M3's only supported path off GENERATED: the report decides.
        equation = source_verified_tree(equation, report)
    if equation is not None:
        forms = EquationForms(spoken_form=equation.root.spoken_form, mathml=equation.mathml,
                              extraction_source=equation.extraction_source)

    label_sources = [p.label_source for p in exploration.parts] + [r.label_source for r in exploration.relationships]
    if table is not None:
        label_sources += [h.text_source for h in table.headers] + [c.text_source for c in table.cells]
    generated_basis = ObservationSource.GENERATED in label_sources or (
        equation is not None and equation.extraction_source is ObservationSource.GENERATED)
    summary = None
    if exploration.summary:
        summary = SourcedText(text=exploration.summary,
                              source=ObservationSource.GENERATED if generated_basis else ObservationSource.OBSERVED)

    draft = dict(evidence_id=evidence_id, source_version_id=source_version_id, evidence_version=evidence_version,
                 object_kind=object_kind, locator=locator, layer=exploration.layer,
                 part_id=part_id if exploration.layer is ExplorationLayer.DETAIL else None,
                 summary=summary, parts=parts, relationships=relationships,
                 unreadable_part_ids=list(exploration.unreadable_part_ids), table=grid, equation=forms,
                 source_check=source_check_for(report), provenance=provenance)
    probe = EvidenceView.model_construct(**draft, ai_description=False)
    return EvidenceView(**draft, ai_description=_any_generated(probe))


__all__ = [
    "EquationForms",
    "EvidenceView",
    "ObjectKind",
    "Part",
    "Provenance",
    "ProvenanceOrigin",
    "Relationship",
    "SourceCheck",
    "SourcedText",
    "TableCellView",
    "TableGrid",
    "TableHeaderView",
    "build_evidence_view",
    "source_check_for",
]
