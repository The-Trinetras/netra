"""Learning-graph projection: PostgreSQL assessment history -> Neo4j.

CLAUDE.md "Data authority": "Neo4j must be rebuildable from canonical
PostgreSQL data" and "A failed Neo4j write must never roll back an
already committed assessment attempt." A projection run is triggered by
draining netra_worker.runtime.outbox after a committed
AssessmentAttempt (see
netra_worker.jobs.learning_projection.neo4j.ProjectAssessmentAttemptJob),
never inline within the same transaction (CLAUDE.md "Background jobs":
"Do not keep a PostgreSQL transaction open while waiting for an
external provider"). This module fixes the application-facing shape;
netra_api.learning.graph.neo4j is the lower-level driver adapter it
depends on.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from netra_api.learning.assessment.models import AssessmentAttempt


class LearningGraphProjector(Protocol):
    """Typed contract for keeping the Neo4j learning graph in sync.

    project_attempt is the incremental path (one outbox event -> one
    mastery edge upsert); rebuild_account is the full, from-scratch
    recompute CLAUDE.md's rebuildability guarantee requires be possible
    at any time.
    """

    async def project_attempt(self, attempt: AssessmentAttempt) -> None:
        ...

    async def rebuild_account(self, account_id: UUID, attempts: list[AssessmentAttempt]) -> None:
        """Recompute this account's entire projected mastery graph from
        its full, authoritative AssessmentAttempt history."""
        ...
