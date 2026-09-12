"""Retrieval service interface — a bounded tool, not an agent.

Combines exact and semantic search over a student's authorized sources.
Pinecone is a derived, rebuildable projection (CLAUDE.md "Data
authority"); hits returned here are NOT yet authorized or resolved to
authoritative text (CLAUDE.md "Evidence rules": "Pinecone result IDs
must be authorized and resolved against PostgreSQL before model context
is constructed"). Callers must pass each hit's evidence_id through
netra_api.content.retrieval.evidence.EvidenceResolver before it reaches
model context.
"""

from __future__ import annotations

from typing import List, Optional, Protocol
from uuid import UUID

from pydantic import BaseModel, Field

from netra_api.platform.auth_context import AuthContext


class RetrievalQuery(BaseModel):
    query_text: str
    source_version_ids: Optional[List[UUID]] = None
    """Restrict the search to these source versions; None searches every
    source authorized for auth.account_id."""
    top_k: int = Field(default=8, ge=1, le=50)


class RetrievalHit(BaseModel):
    """One unauthorized, unresolved search result — not safe to show a student as-is."""

    evidence_id: str
    score: float


class RetrievalService(Protocol):
    """Typed contract the Coordinator's retrieval tool calls through."""

    async def search(self, auth: AuthContext, query: RetrievalQuery) -> List[RetrievalHit]:
        ...
