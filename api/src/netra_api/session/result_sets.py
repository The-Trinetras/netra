"""Server-side storage for the "last stable result set" a session references.

Approved design (2026-09-12, session.snapshot decision E — Option B):
SessionState.last_result_set (see session/state.py) holds only a
{result_set_id, created_at} reference. The ordered evidence list itself
lives here, under the Session service's existing ownership of "stable
last result set" (data-ownership.md), so a session.snapshot never needs
to re-transmit full result content and "open the third one" always
resolves against exactly the list the student was told about.

Storing evidence_ids rather than resolved Evidence bodies keeps this
table cheap and correct: the bodies are already durable and re-resolvable
through content.retrieval.evidence.EvidenceResolver, which independently
re-checks authorization, deletion and source-version compatibility every
time — a result set surviving longer than the evidence it points to must
not bypass that check.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Protocol
from uuid import UUID

from pydantic import BaseModel, Field


class ResultSetItem(BaseModel):
    """One entry in a result set, in presentation order."""

    ordinal: int = Field(ge=0)
    """0-based position. "Open the third one" resolves to ordinal 2."""
    evidence_id: str
    label: Optional[str] = None
    """Short display text (e.g. a source title) for accessible re-announcement.
    Never the resolved evidence body — that is re-fetched through
    EvidenceResolver at open-time, not cached here."""


class ResultSet(BaseModel):
    """A stored, ordered list of results one search response presented.

    Bounded and short-lived: this is not a search history log. A newer
    completed search replaces the session's last_result_set reference;
    this row simply becomes unreferenced and eligible for cleanup after
    its TTL. Superseding a *pending* result set (a search still forming
    its response) is a race the response formatter must avoid by writing
    this row once atomically before it is referenced from SessionState,
    not something this model resolves.
    """

    result_set_id: UUID
    session_id: UUID
    source_version_id: str
    """Pins the result set to the source version active when it was produced,
    matching the session's own source-version pinning."""
    created_at: datetime
    expires_at: datetime
    items: List[ResultSetItem] = Field(default_factory=list, max_length=10)
    """Bounded to keep "read at most three initially, preserve stable
    numbering" (Engineering Plan §3.2) cheap to store and re-announce."""


class ResultSetRepository(Protocol):
    """Typed contract for creating and resolving stored result sets.

    A concrete PostgreSQL-backed implementation is added alongside the
    result_sets table migration, not here (CLAUDE.md: agents/services
    never hold raw database connections outside their owning module).
    """

    def create(self, result_set: ResultSet) -> ResultSet:
        ...

    def get(self, session_id: UUID, result_set_id: UUID) -> Optional[ResultSet]:
        """Return the result set if it exists, belongs to session_id, and has
        not expired; otherwise None. A None here is a stale-reference case
        for the caller to report as RESOURCE_UNAVAILABLE/STALE_REQUEST —
        never silently resolved against a different list."""
        ...
