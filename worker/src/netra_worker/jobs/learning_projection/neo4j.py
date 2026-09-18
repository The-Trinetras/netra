"""Learning-graph projection background job: one committed attempt -> one factual edge.

Drains the outbox event written alongside a committed AssessmentAttempt
and projects it into Neo4j (CLAUDE.md "Background jobs": "Use an outbox
when a committed PostgreSQL mutation requires a later
projection/update"). A projection failure only fails and retries this
job; it never touches the committed PostgreSQL attempt (learning.md: "A
projection failure never reverses an already committed assessment").

**Factual, not a mastery label (D4, docs/team/handoffs/M4.md).** The
legacy projection shape, netra_api.learning.graph.neo4j.MasteryEdge,
requires a LearningStatus, which only exists by running the removed
automatic mastery derivation. That shape and its interface stay in place
untouched as legacy. This job instead projects an ATTEMPTED relationship
carrying the recorded facts of one specific attempt — outcome, hints used,
who evaluated it, when — never an aggregate judgement about the student.

**No worker AuthContext.** Account-scoped repositories require an
AuthContext, and no worker-scoped authority exists. Rather than invent a
privileged one, the outbox payload carries every fact the projection
needs, written in the same transaction as the attempt, so the worker
projects from the event and never re-reads account-scoped state. The
payload fields beyond account_id/attempt_id are PROPOSED (D4) and need
M2's outbox port and review before they are relied on in production.

**Idempotent and order-independent.** Each relationship is keyed by its
immutable attempt_id and its properties are set only when it is created.
Replaying the same event changes nothing; an older event arriving after a
newer one creates or leaves its own relationship and cannot overwrite a
different attempt's facts, because attempts are append-only and nothing
here aggregates across them. Rebuilding an account is replaying its
committed attempts.

The worker imports nothing from netra_api at runtime, so the payload
shape here is kept in step with
netra_api.learning.graph.factual.attempt_projection_payload by a
cross-package test, not by an import. A shared job schema under
shared/contracts/jobs/v1/ would replace that test; it needs M2/M4 review.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from netra_worker.runtime.job_repository import JobPayload

LEARNING_PROJECTION_JOB_TYPE = "learning.project_assessment_attempt"


class ProjectAssessmentAttemptPayload(JobPayload):
    """The outbox payload for one committed attempt.

    account_id and attempt_id existed before; the remaining fields are the
    PROPOSED D4 additions that let the worker project without re-reading
    account-scoped state. Extra fields are rejected so a drifted producer
    fails loudly instead of being half-projected.
    """

    model_config = ConfigDict(extra="forbid")

    account_id: UUID
    attempt_id: UUID
    concept_id: str = Field(min_length=1)
    outcome: Literal["correct", "incorrect", "partial"]
    hints_used: int = Field(ge=0)
    evaluated_by: Literal["tutor", "grader"]
    occurred_at: datetime
    """The committed attempt's created_at."""

    @field_validator("occurred_at")
    @classmethod
    def _timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        return value


class AttemptedEdge(BaseModel):
    """One (student)-[:ATTEMPTED]->(concept) relationship: the facts of one attempt."""

    model_config = ConfigDict(frozen=True)

    account_id: UUID
    concept_id: str
    attempt_id: UUID
    outcome: Literal["correct", "incorrect", "partial"]
    hints_used: int
    evaluated_by: Literal["tutor", "grader"]
    occurred_at: datetime


class ProjectionWrite(str, Enum):
    PROJECTED = "projected"
    """The relationship for this attempt_id exists: created now, or left
    exactly as it was by a replay."""
    CONCEPT_NOT_PROJECTED = "concept_not_projected"
    """The concept node does not exist yet. Nothing was written: this job
    must not create concepts from attempt data (learning.md: "Do not
    silently create concepts or prerequisite relationships from Tutor
    output"). Concept nodes come from canonical concept records."""


ATTEMPTED_EDGE_CYPHER = """MATCH (c:Concept {concept_id: $concept_id})
MERGE (s:Student {account_id: $account_id})
MERGE (s)-[a:ATTEMPTED {attempt_id: $attempt_id}]->(c)
ON CREATE SET
  a.outcome = $outcome,
  a.hints_used = $hints_used,
  a.evaluated_by = $evaluated_by,
  a.occurred_at = datetime($occurred_at)
RETURN a.attempt_id AS attempt_id
"""
"""The statement a concrete Neo4j writer executes (Neo4j 5.26-compatible).

Reviewed shape, not yet executed against a database (no driver or
database is available here). Properties are written ON CREATE only, so a
replay cannot alter a projected attempt. MATCH on Concept, never MERGE,
so a missing concept yields no rows (CONCEPT_NOT_PROJECTED) instead of a
fabricated node. Parameters are bound, never interpolated.

Concurrency caveat for M2 review: MERGE alone does not stop two workers
delivering the same event at the same instant from both creating the
relationship. The job runtime's one-lease-per-idempotency-key claim is the
guard assumed here; a relationship key constraint on attempt_id would be a
second one, but its availability depends on the Neo4j edition and is not
assumed."""


class FactualLearningGraphWriter(Protocol):
    """Worker-side port to Neo4j for the factual projection.

    A concrete implementation wraps the approved neo4j==6.3.0 async
    driver, runs ATTEMPTED_EDGE_CYPHER with bound parameters (UUIDs and
    occurred_at as ISO-8601 strings), and maps "no rows" to
    CONCEPT_NOT_PROJECTED and one row to PROJECTED. No driver is imported
    here.
    """

    async def upsert_attempted(self, edge: AttemptedEdge) -> ProjectionWrite:
        ...


class ConceptNotProjectedError(Exception):
    """Retryable: the concept node has not been projected yet.

    Raised so the job runtime reschedules this job with backoff rather
    than completing it with nothing written. Concept projection is a
    separate, canonical-record-driven path.
    """

    def __init__(self, concept_id: str) -> None:
        self.concept_id = concept_id
        super().__init__(f"concept {concept_id!r} is not projected yet")


def attempted_edge_from(payload: ProjectAssessmentAttemptPayload) -> AttemptedEdge:
    return AttemptedEdge(
        account_id=payload.account_id,
        concept_id=payload.concept_id,
        attempt_id=payload.attempt_id,
        outcome=payload.outcome,
        hints_used=payload.hints_used,
        evaluated_by=payload.evaluated_by,
        occurred_at=payload.occurred_at,
    )


class ProjectAssessmentAttemptJob:
    """Structurally implements
    netra_worker.runtime.job_repository.JobHandler[ProjectAssessmentAttemptPayload].

    Safe to re-run: the write is keyed by attempt_id and is create-only.
    """

    def __init__(self, writer: FactualLearningGraphWriter) -> None:
        self._writer = writer

    async def handle(self, payload: ProjectAssessmentAttemptPayload) -> None:
        outcome = await self._writer.upsert_attempted(attempted_edge_from(payload))
        if outcome is ProjectionWrite.CONCEPT_NOT_PROJECTED:
            raise ConceptNotProjectedError(payload.concept_id)
