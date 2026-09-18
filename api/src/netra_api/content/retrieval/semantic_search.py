"""Semantic candidate search over the rebuildable Pinecone projection.

Scope is applied before the query (account and, when pinned, source versions)
and every match is still resolved against PostgreSQL afterwards. Vectors are
only comparable when produced by the same pinned embedding specification, so
the query filters on ``embedding_spec`` and drops any returned match whose
spec differs: after a model or dimension change, old-spec vectors can never
rank against a new-spec query (backend-data.md: "Do not mix incompatible
document/query embeddings").
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from netra_api.content.providers.pinecone import VectorIndexProvider
from netra_api.content.retrieval.embeddings import EmbeddingProvider, EmbeddingSpec
from netra_api.content.retrieval.exact_search import SearchCandidate
from netra_api.content.settings import ContentSettings
from netra_api.platform.auth_context import AuthContext


class IncompatibleEmbeddingError(RuntimeError):
    """The query embedding does not match the pinned embedding specification."""


class SemanticSearch(Protocol):
    async def search(self, auth: AuthContext, query_text: str, source_version_ids: list[UUID] | None,
                     top_k: int) -> list[SearchCandidate]: ...


class PineconeSemanticSearch:
    def __init__(self, embedder: EmbeddingProvider, index: VectorIndexProvider,
                 namespace: str = "netra", spec: EmbeddingSpec | None = None) -> None:
        self.embedder, self.index, self.namespace = embedder, index, namespace
        self.spec = spec or EmbeddingSpec.from_settings(ContentSettings())

    async def search(self, auth: AuthContext, query_text: str, source_version_ids: list[UUID] | None,
                     top_k: int) -> list[SearchCandidate]:
        if not query_text.strip():
            return []
        if source_version_ids is not None and not source_version_ids:
            return []
        embedding = await self.embedder.embed_query(query_text)
        if len(embedding) != self.spec.dimension:
            raise IncompatibleEmbeddingError("query embedding dimension does not match the pinned specification")
        metadata_filter: dict = {"account_id": str(auth.account_id), "embedding_spec": self.spec.version}
        if source_version_ids is not None:
            metadata_filter["source_version_id"] = {"$in": [str(i) for i in source_version_ids]}
        matches = await self.index.query(self.namespace, embedding, min(top_k, 20), metadata_filter)
        return [SearchCandidate(evidence_id=match.id, score=match.score) for match in matches
                if match.metadata.get("embedding_spec") == self.spec.version
                and match.metadata.get("account_id") == str(auth.account_id)]
