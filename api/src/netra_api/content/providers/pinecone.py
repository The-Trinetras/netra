"""Vector-index provider interface.

Pinecone is a derived, rebuildable projection over PostgreSQL (CLAUDE.md
"Data authority"); it is never authoritative, and a failed write here
must never roll back an already-committed PostgreSQL mutation. No
Pinecone SDK call is implemented here — see
netra_api.content.retrieval.evidence.EvidenceResolver, which authorizes
and resolves any ID this provider returns before it reaches model
context. No provider is wired up in this scaffold.
"""

from __future__ import annotations

from typing import List, Protocol, Tuple

from pydantic import BaseModel


class VectorMatch(BaseModel):
    id: str
    score: float


class VectorIndexProvider(Protocol):
    """Typed contract for the derived vector index."""

    async def upsert(self, namespace: str, vectors: List[Tuple[str, List[float]]]) -> None:
        ...

    async def query(self, namespace: str, embedding: List[float], top_k: int) -> List[VectorMatch]:
        ...

    async def delete(self, namespace: str, ids: List[str]) -> None:
        ...
