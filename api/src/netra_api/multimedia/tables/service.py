"""Table-extraction bounded tool interface.

Not an agent (CLAUDE.md "Architecture invariants"). M3 owns extraction
semantics and validation; M2 owns the storage and the source/version
identity behind it (multimedia.md: "M3 validates table extraction; M2
owns source/table storage and version identity"), so this Protocol is
the read side only. The write side is TableCandidateSink, which is what
M3's worker job hands its reviewed output to; M2 supplies the
implementation that persists it and activates the source version.

Splitting read from write keeps the ownership line visible in the type
system: nothing in netra_api.multimedia can write a table, and nothing
in M2 has to know how a table was extracted.
"""

from __future__ import annotations

from typing import List, Optional, Protocol
from uuid import UUID

from netra_api.multimedia.tables.models import TableStructure
from netra_api.multimedia.validation import ValidationReport
from netra_api.platform.auth_context import AuthContext


class TableService(Protocol):
    """Typed contract for retrieving already-extracted, stored tables.

    Implementations must authorize source_version_id against
    auth.account_id and must call
    netra_api.multimedia.evidence.resolve_and_authorize on every
    TableStructure.reference before returning it.
    """

    async def get_table(
        self, auth: AuthContext, source_version_id: UUID, table_index: int
    ) -> TableStructure:
        ...

    async def list_tables(
        self, auth: AuthContext, source_version_id: UUID
    ) -> List[TableStructure]:
        ...


class TableCandidateSink(Protocol):
    """Where M3's extraction hands a candidate table to M2 for storage.

    PROPOSED boundary: M2 owns the implementation and the migration
    behind it, and has not reviewed this shape yet. It is recorded in
    docs/team/handoffs/M3.md rather than assumed approved.

    The validation report travels with the candidate on purpose. A store
    that receives only the structure cannot tell a table checked against
    the original media from one a model produced and nobody looked at,
    and would have to decide for itself whether to activate it.
    """

    async def store_candidate(
        self,
        source_version_id: UUID,
        table: TableStructure,
        validation: ValidationReport,
        idempotency_key: str,
    ) -> None:
        """Persist one extracted table candidate. Idempotent by idempotency_key.

        Must not mark the table citable on its own: registering DERIVED
        evidence stays with the API service layer that authorizes it.
        """
        ...


def citable_tables(
    tables: List[TableStructure], reports: dict[UUID, ValidationReport]
) -> List[TableStructure]:
    """Narrow tables to those whose validation actually passed a source check.

    Used where a caller is about to present table values as facts about
    the document. A table with no report is excluded: absence of a check
    is not a pass (see netra_api.multimedia.validation).
    """

    return [
        table
        for table in tables
        if (report := reports.get(table.table_id)) is not None and report.is_source_verified
    ]


def first_unverified_reason(report: Optional[ValidationReport]) -> Optional[str]:
    """A short, student-safe explanation of why a table is not source-verified.

    Returns None when the table is verified. The wording names the
    limitation without echoing internal rejection vocabulary.
    """

    if report is None:
        return "this table has not been checked against the document"
    if report.is_source_verified:
        return None
    if report.mismatches:
        return "the extracted table does not match the document"
    if report.unreadable:
        return "part of this table could not be read in the document"
    if report.unsupported:
        return "part of this table is not something Netra can extract yet"
    return "this table has not been checked against the document"
