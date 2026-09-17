from __future__ import annotations

from typing import Protocol
from uuid import UUID

from netra_api.content.providers.pinecone import VectorIndexProvider
from netra_api.content.retrieval.embeddings import EmbeddingProvider
from netra_api.content.retrieval.exact_search import SearchCandidate
from netra_api.platform.auth_context import AuthContext


class SemanticSearch(Protocol):
    async def search(self, auth: AuthContext, query_text: str, source_version_ids: list[UUID] | None,
                     top_k: int) -> list[SearchCandidate]: ...


class PineconeSemanticSearch:
    def __init__(self, embedder: EmbeddingProvider, index: VectorIndexProvider,
                 namespace: str = "netra") -> None:
        self.embedder, self.index, self.namespace = embedder, index, namespace

    async def search(self, auth: AuthContext, query_text: str, source_version_ids: list[UUID] | None,
                     top_k: int) -> list[SearchCandidate]:
        if not query_text.strip():
            return []
        embedding = await self.embedder.embed_query(query_text)
        metadata_filter = {"account_id": str(auth.account_id)}
        if source_version_ids is not None:
            metadata_filter["source_version_id"] = {"$in": [str(i) for i in source_version_ids]}
        matches = await self.index.query(self.namespace, embedding, min(top_k, 20), metadata_filter)
        return [SearchCandidate(evidence_id=match.id, score=match.score) for match in matches]
