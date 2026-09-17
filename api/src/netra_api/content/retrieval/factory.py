"""Small application composition boundary for retrieval.

The API currently has no startup-level retrieval composition root. This
factory keeps construction in one place until that application wiring exists.
It does not initialize model weights; ``BGEReranker`` remains lazy.
"""

from __future__ import annotations

from netra_api.config import Settings
from netra_api.content.providers.pinecone import PineconeVectorIndex
from netra_api.content.retrieval.embeddings import GeminiEmbeddingProvider
from netra_api.content.retrieval.exact_search import PostgresExactSearch
from netra_api.content.retrieval.postgres_evidence import AsyncPostgresEvidenceResolver
from netra_api.content.retrieval.reranker import BGEReranker
from netra_api.content.retrieval.semantic_search import PineconeSemanticSearch
from netra_api.content.retrieval.service import HybridRetrievalService
from sqlalchemy.ext.asyncio import AsyncSession


def build_hybrid_retrieval_service(lexical, semantic, evidence_resolver,
                                   settings: Settings | None = None) -> HybridRetrievalService:
    """Build the bounded retrieval graph using typed runtime settings."""
    settings = settings or Settings()
    reranker = BGEReranker(settings) if settings.reranker_enabled else None
    return HybridRetrievalService(lexical, semantic, evidence_resolver, reranker, settings)


def build_postgres_retrieval_service(session: AsyncSession,
                                     settings: Settings | None = None) -> HybridRetrievalService:
    """Build the concrete API retrieval graph for one request/session.

    The caller owns the session lifecycle. This function creates no engine,
    session factory, network connection, or model weights; provider calls and
    BGE initialization remain lazy behind their existing adapters.
    """
    settings = settings or Settings()
    embeddings = GeminiEmbeddingProvider(settings)
    index = PineconeVectorIndex(settings)
    return build_hybrid_retrieval_service(
        PostgresExactSearch(session),
        PineconeSemanticSearch(embeddings, index, settings.pinecone_namespace),
        AsyncPostgresEvidenceResolver(session),
        settings,
    )
