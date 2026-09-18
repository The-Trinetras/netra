"""Registered extraction adapter behind the worker's ObjectExtractionPort.

``ParserBackedObjectExtraction`` implements
netra_worker.jobs.multimedia.extraction.ObjectExtractionPort structurally:

1. parse the object crop with the approved document parser, through M2's
   DocumentParserProvider port (netra_api.content.providers.llamaparse);
2. convert the parser's output deterministically (extraction.tables /
   extraction.equations);
3. look up the reviewer's record of the original media and validate the
   structure against it (tables/validation.py, equations/validation.py);
4. return the structure with its verdict and findings, always together.

The return value is JSON-compatible data shaped like the worker's
ExtractionOutcome. The worker job re-validates it into its own record
(the worker imports nothing from netra_api), so this is the one place
the two processes' shapes meet, and the worker rejects any drift.

Figures, charts and diagrams raise ExtractionUnsupportedError. No
approved extractor reads axes, scales or connectivity: LlamaParse and
Tesseract return text, and text alone cannot establish which label is
the x axis or which boxes are connected. Returning a text-only
"structure" would look like a reading of the figure. The decision on a
structural visual extractor is recorded as M3-VIS-1 in the M3 handoff.

Evidence ids and locators. A candidate is not evidence yet: its
reference carries a ``pending:`` evidence id that no resolver will
authorize, and a non-private ``kind:index`` locator. M2's registration
assigns the canonical evidence id and page/region locator (the job
payload does not carry one; see INT-04 in the handoff).
"""

from __future__ import annotations

import functools
import uuid
from typing import Any, List, Optional, Protocol, Sequence
from uuid import UUID

from netra_api.multimedia.equations.models import EquationEvidenceReference
from netra_api.multimedia.equations.validation import (
    EquationSourceCheck,
    source_verified_tree,
    validate_equation_against_source,
)
from netra_api.multimedia.extraction.equations import parse_basic_equation
from netra_api.multimedia.extraction.tables import looks_like_markdown_table, parse_markdown_table
from netra_api.multimedia.providers.calls import CancellationSignal, call_provider
from netra_api.multimedia.providers.errors import (
    ExtractionUnsupportedError,
    MalformedProviderResponseError,
    error_code,
)
from netra_api.multimedia.tables.models import TableEvidenceReference
from netra_api.multimedia.tables.validation import TableSourceCheck, validate_table_against_source
from netra_api.multimedia.tracing import media_span, record_outcome
from netra_api.multimedia.validation import ValidationReport
from netra_api.platform.tracing import Tracer

TABLE_HINTS = frozenset({"table"})
EQUATION_HINTS = frozenset({"equation", "formula", "math"})
UNSUPPORTED_KINDS = frozenset({"figure", "chart", "diagram"})

CANDIDATE_NAMESPACE = uuid.UUID("5c1f6f5e-9a43-4c1e-8f7a-3d0d6a4b2e11")
"""Namespace for deterministic candidate ids: re-extracting the same object
of the same source version yields the same table/equation id, so repeated
processing preserves canonical references and navigation ids."""


class ParsedBlockLike(Protocol):
    text: str
    block_type_hint: str


class DocumentParser(Protocol):
    """M2's DocumentParserProvider shape."""

    async def parse(self, object_key: str, content_type: str) -> Sequence[ParsedBlockLike]:
        ...


class SourceCheckLookup(Protocol):
    """Reviewer records of the original media, by object. M2 stores them.

    Returns None when no reviewer has recorded this object: the result is
    then NOT_CHECKED and never citable. The lookup must be scoped to the
    source version; a record for another version is not a check of this one.
    """

    async def table_check(self, *, source_version_id: UUID, table_index: int) -> Optional[TableSourceCheck]:
        ...

    async def equation_check(self, *, source_version_id: UUID, equation_index: int) -> Optional[EquationSourceCheck]:
        ...


def candidate_id(source_version_id: UUID, kind: str, object_index: int) -> UUID:
    return uuid.uuid5(CANDIDATE_NAMESPACE, f"{source_version_id}:{kind}:{object_index}")


def verdict_of(report: ValidationReport) -> dict[str, Any]:
    """Counts shaped like the worker's ValidationVerdict."""

    return {
        "source_verified": report.is_source_verified,
        "checked_count": len(report.checked_findings),
        "mismatch_count": len(report.mismatches),
        "unreadable_count": len(report.unreadable),
        "unsupported_count": len(report.unsupported),
        "summary": report.summary(),
    }


def uncertainty_of(report: ValidationReport) -> str:
    if not report.checked_findings:
        return "not_checked"
    if report.unreadable:
        return "unreadable"
    if report.is_source_verified:
        return "observed"
    return "generated"


class ParserBackedObjectExtraction:
    """ObjectExtractionPort over the approved document parser; see module docstring."""

    def __init__(
        self,
        parser: DocumentParser,
        source_checks: SourceCheckLookup,
        *,
        parser_provider: str,
        crop_content_type: str,
        tracer: Optional[Tracer] = None,
        cancellation: Optional[CancellationSignal] = None,
    ) -> None:
        if not parser_provider or not crop_content_type:
            raise ValueError("parser_provider and crop_content_type are explicit configuration")
        self._parser = parser
        self._checks = source_checks
        self._provider = parser_provider
        self._content_type = crop_content_type
        self._tracer = tracer
        self._cancellation = cancellation

    async def extract(
        self,
        *,
        object_key: str,
        kind: Any,
        object_index: int,
        source_version_id: UUID,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        kind_value = str(getattr(kind, "value", kind))
        with media_span(
            self._tracer,
            "media.extract",
            operation="extract_object",
            provider=self._provider,
            source_version_id=source_version_id,
            stage="extract_object",
            object_kind=kind_value,
        ) as span:
            if kind_value in UNSUPPORTED_KINDS:
                error = ExtractionUnsupportedError(
                    self._provider, f"no approved structural extractor for {kind_value}"
                )
                record_outcome(span, "unsupported", uncertainty="not_checked")
                span.fail("error", error_code(error))
                raise error
            if kind_value not in ("table", "equation"):
                raise ValueError(f"unknown extraction kind {kind_value}")

            blocks = await call_provider(
                functools.partial(self._parser.parse, object_key, self._content_type),
                provider=self._provider,
                operation="parse_object",
                timeout_seconds=timeout_seconds,
                cancellation=self._cancellation,
                tracer=self._tracer,
            )
            text = _single_block(blocks, kind_value, provider=self._provider)

            if kind_value == "table":
                structure, report = await self._table(text, source_version_id, object_index)
            else:
                structure, report = await self._equation(text, source_version_id, object_index)

            record_outcome(
                span,
                "verified" if report.is_source_verified else "unverified",
                uncertainty=uncertainty_of(report),
                netra_check="source_check",
                netra_rejected_count=len(report.mismatches),
            )
            return {
                "kind": kind_value,
                "object_index": object_index,
                "structure": structure,
                "validation": verdict_of(report),
                "findings": [finding.model_dump(mode="json") for finding in report.findings],
            }

    async def _table(self, text: str, source_version_id: UUID, index: int) -> tuple[dict[str, Any], ValidationReport]:
        reference = TableEvidenceReference(
            evidence_id=f"pending:table:{index}",
            source_version_id=source_version_id,
            locator=f"table:{index}",
            table_index=index,
        )
        table = parse_markdown_table(
            text, table_id=candidate_id(source_version_id, "table", index), reference=reference, id_prefix=f"tbl{index:02d}"
        )
        check = await _maybe_await(self._checks.table_check(source_version_id=source_version_id, table_index=index))
        report = validate_table_against_source(table, check) if check is not None else ValidationReport(object_id=str(table.table_id))
        return table.model_dump(mode="json"), report

    async def _equation(self, text: str, source_version_id: UUID, index: int) -> tuple[dict[str, Any], ValidationReport]:
        reference = EquationEvidenceReference(
            evidence_id=f"pending:equation:{index}",
            source_version_id=source_version_id,
            locator=f"equation:{index}",
            equation_index=index,
        )
        tree = parse_basic_equation(
            text, equation_id=candidate_id(source_version_id, "equation", index), reference=reference, id_prefix=f"eq{index:02d}"
        )
        check = await _maybe_await(self._checks.equation_check(source_version_id=source_version_id, equation_index=index))
        if check is None:
            return tree.model_dump(mode="json"), ValidationReport(object_id=str(tree.equation_id))
        report = validate_equation_against_source(tree, check)
        return source_verified_tree(tree, report).model_dump(mode="json"), report


def _single_block(blocks: Any, kind: str, *, provider: str) -> str:
    hints = TABLE_HINTS if kind == "table" else EQUATION_HINTS
    matching: List[str] = []
    for block in list(blocks or []):
        hint = str(getattr(block, "block_type_hint", "") or "").lower()
        text = getattr(block, "text", None)
        if not isinstance(text, str) or not text.strip():
            continue
        if hint in hints or (kind == "table" and looks_like_markdown_table(text)):
            matching.append(text)
    if not matching:
        raise MalformedProviderResponseError(provider, f"parser found no {kind} in the object crop", field="blocks")
    if len(matching) > 1:
        raise MalformedProviderResponseError(
            provider, f"parser found {len(matching)} {kind}s in one object crop; which one is ambiguous", field="blocks"
        )
    return matching[0]


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value
