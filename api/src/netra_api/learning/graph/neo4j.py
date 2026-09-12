"""Neo4j provider adapter interface for the learning knowledge graph.

Neo4j is a derived, rebuildable projection over PostgreSQL (CLAUDE.md
"Data authority": "Neo4j must be rebuildable from canonical PostgreSQL
data."). A failed Neo4j write must never roll back an already-committed
PostgreSQL mutation (CLAUDE.md: "A failed Neo4j write must never roll
back an already committed assessment attempt") — see
netra_worker.runtime.outbox, which is how a projection run gets
triggered after commit rather than inline.

No neo4j driver import and no network calls happen here — see
docs/architecture/runtime-baseline.md, which pins neo4j==6.3.0 as an
approved-but-not-yet-installed dependency ("Use Neo4j 6.x driver APIs
and Neo4j 5.26-compatible queries"). Callers get typed upsert
operations rather than a raw-query method, mirroring
netra_api.content.providers.pinecone.VectorIndexProvider, so no caller
authors Cypher directly (CLAUDE.md "Database boundaries": no
model-generated queries; database access belongs inside
repository/service modules).
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Protocol
from uuid import UUID

from pydantic import BaseModel

from netra_api.learning.assessment.models import LearningStatus


class ConceptNode(BaseModel):
    """One concept vertex. Concept definitions are canonical PostgreSQL
    records (CLAUDE.md "Data authority"); this is only their projection."""

    concept_id: str
    label: str


class PrerequisiteEdge(BaseModel):
    concept_id: str
    prerequisite_concept_id: str


class MasteryEdge(BaseModel):
    """One account's projected status for one concept.

    status mirrors netra_api.learning.assessment.models.LearningStatus —
    Neo4j never computes or owns this label, it only mirrors what
    PostgreSQL already decided (CLAUDE.md "Data authority").
    """

    account_id: UUID
    concept_id: str
    status: LearningStatus
    last_assessed_at: Optional[datetime] = None

    observed_at: datetime
    """Ordering key for the projection, taken from the created_at of the
    AssessmentAttempt this edge was derived from.

    Required because outbox delivery is at-least-once and unordered: a
    retried older event can arrive after a newer one. Without a
    comparable key the older event would overwrite newer projected state,
    which backend-data.md forbids ("Older retries must not overwrite
    newer projected state")."""

    source_attempt_id: UUID
    """The canonical PostgreSQL attempt this edge was projected from, so a
    replayed event is recognisable as the same write."""


class LearningGraphProvider(Protocol):
    """Typed contract for the Neo4j 6.x driver boundary.

    A concrete implementation wraps a neo4j.AsyncGraphDatabase session;
    business logic never touches driver Record/Result objects directly
    (CLAUDE.md "Provider adapters").
    """

    async def upsert_concepts(self, concepts: list[ConceptNode]) -> None:
        ...

    async def upsert_prerequisites(self, edges: list[PrerequisiteEdge]) -> None:
        ...

    async def upsert_mastery(self, edges: list[MasteryEdge]) -> None:
        """Project mastery edges, newest-wins and replay-safe.

        Implementations must not apply an edge whose observed_at is older
        than the observed_at already stored for the same
        (account_id, concept_id). Re-applying the same edge must leave
        the projection unchanged.

        Both properties are required by backend-data.md ("Projection
        writes must be idempotent and version-aware. Older retries must
        not overwrite newer projected state") and hold at the Cypher
        level, not in the caller: two workers may drain overlapping
        outbox events concurrently, so the guard has to be in the write
        itself."""
        ...

    async def delete_account_mastery(self, account_id: UUID) -> None:
        """Used when a full rebuild replaces one account's projected mastery."""
        ...
