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

from pydantic import BaseModel, Field, field_validator

from netra_api.platform.auth_context import AuthContext
from netra_api.content.settings import ContentSettings
from netra_api.content.telemetry import increment, log_event, observe, stage_span
from netra_api.platform.tracing import DISABLED_TRACER, Tracer
import logging
import time


class RetrievalUnavailableError(RuntimeError):
    """Both bounded retrieval providers were unavailable."""


class RetrievalProviderUnavailableError(RuntimeError):
    """An explicitly classified provider-availability failure."""


def _semantic_failure_types() -> tuple[type[BaseException], ...]:
    """Failures of the rebuildable semantic projection path.

    Gemini query embedding and the Pinecone index are derived, optional
    infrastructure (overview.md "Pinecone failure permits the specified
    authorized PostgreSQL text-search reduced mode"). Their configuration and
    provider failures therefore mean "semantic unavailable", not "search
    failed"; the lexical PostgreSQL path still answers. Imported lazily to
    keep this interface module free of provider adapters.
    """

    from netra_api.content.providers.pinecone import VectorIndexError
    from netra_api.content.retrieval.embeddings import EmbeddingError
    from netra_api.content.retrieval.semantic_search import IncompatibleEmbeddingError

    return (EmbeddingError, VectorIndexError, IncompatibleEmbeddingError)


class RetrievalQuery(BaseModel):
    query_text: str
    source_version_ids: Optional[List[UUID]] = None
    """Restrict the search to these source versions; None searches every
    source authorized for auth.account_id."""
    top_k: int = Field(default=6, ge=1, le=50)

    @field_validator("top_k")
    @classmethod
    def normalize_final_range(cls, value: int) -> int:
        return min(max(value, 4), 6)


class RetrievalHit(BaseModel):
    """One unauthorized, unresolved search result — not safe to show a student as-is."""

    evidence_id: str
    score: float


class RetrievalService(Protocol):
    """Typed contract the Coordinator's retrieval tool calls through."""

    async def search(self, auth: AuthContext, query: RetrievalQuery) -> List[RetrievalHit]:
        ...


class HybridRetrievalService:
    """Bounded lexical + semantic retrieval with canonical evidence gating."""

    def __init__(self, lexical, semantic, evidence_resolver, reranker=None,
                 settings: ContentSettings | None = None, tracer: Tracer | None = None) -> None:
        self.lexical, self.semantic = lexical, semantic
        self.evidence_resolver, self.reranker = evidence_resolver, reranker
        self.settings = settings or ContentSettings()
        # Process telemetry is configured once by the composition root; a
        # service constructor must not reconfigure it as a side effect.
        self.tracer = tracer or DISABLED_TRACER

    async def search(self, auth: AuthContext, query: RetrievalQuery) -> list[RetrievalHit]:
        from netra_api.content.retrieval.ranking import reciprocal_rank_fusion
        if not query.query_text.strip():
            return []
        import asyncio
        started = time.perf_counter()
        # An explicit version scope is a pinned session: its versions stay
        # servable after a newer version activates. Unscoped search serves
        # only each source's active version.
        require_active = query.source_version_ids is None
        increment("netra_retrieval_requests_total")
        log_event(logging.getLogger(__name__), "retrieval_started", component="retrieval")

        async def provider_call(provider, name, top_k):
            stage = time.perf_counter()
            try:
                with stage_span(self.tracer, f"retrieval.{name}", operation=f"retrieval_{name}"):
                    result = await provider.search(auth, query.query_text, query.source_version_ids, top_k)
                return result
            except Exception as exc:
                increment("netra_retrieval_failures_total")
                increment("netra_provider_failures_total", provider=name, operation="search")
                log_event(logging.getLogger(__name__), "retrieval_provider_failed", component="retrieval",
                          provider=name, error_type=type(exc).__name__, level=logging.ERROR)
                if name == "pinecone" and isinstance(exc, _semantic_failure_types()):
                    raise RetrievalProviderUnavailableError(type(exc).__name__) from None
                raise
            finally:
                observe("netra_retrieval_provider_duration_seconds", time.perf_counter() - stage, provider=name)

        lexical_task = asyncio.create_task(provider_call(self.lexical, "fts", self.settings.fts_top_k))
        semantic_task = asyncio.create_task(provider_call(self.semantic, "pinecone", self.settings.vector_top_k))
        lexical_result, semantic_result = await asyncio.gather(lexical_task, semantic_task,
                                                                return_exceptions=True)
        lexical_unavailable = isinstance(lexical_result, RetrievalProviderUnavailableError)
        semantic_unavailable = isinstance(semantic_result, RetrievalProviderUnavailableError)
        if isinstance(lexical_result, BaseException) and not lexical_unavailable:
            raise lexical_result
        if isinstance(semantic_result, BaseException) and not semantic_unavailable:
            raise semantic_result
        lexical = [] if lexical_unavailable else lexical_result
        semantic = [] if semantic_unavailable else semantic_result
        if lexical_unavailable and semantic_unavailable:
            raise RetrievalUnavailableError("lexical and semantic retrieval are unavailable")
        stage = time.perf_counter()
        with stage_span(self.tracer, "retrieval.fusion", operation="retrieval_rrf"):
            fused = reciprocal_rank_fusion(lexical, semantic, k=self.settings.rrf_k,
                                           top_k=self.settings.fusion_top_k)
        observe("netra_retrieval_provider_duration_seconds", time.perf_counter() - stage, provider="rrf")
        if self.reranker is not None:
            rerank_candidates = fused[:self.settings.rerank_top_k]
            # Resolve canonical text before local model inference so neither
            # provider metadata nor an opaque ID can cause unauthorized text
            # to reach the reranker. The final resolution below remains the
            # authoritative delivery gate.
            pre_resolved = await self.evidence_resolver.resolve(
                auth,
                [hit.evidence_id for hit in rerank_candidates],
                allowed_source_version_ids=query.source_version_ids,
                require_active=require_active,
            )
            resolved_by_id = {
                item.evidence_id: item.evidence.text
                for item in pre_resolved
                if item.is_resolved and item.evidence is not None
            }
            rerank_candidates = [
                hit.model_copy(update={"text": resolved_by_id[hit.evidence_id]})
                for hit in rerank_candidates
                if hit.evidence_id in resolved_by_id
            ]
            stage = time.perf_counter()
            try:
                fused = await self.reranker.rerank(query.query_text, rerank_candidates)
            except RetrievalProviderUnavailableError:
                # Reranking is an optional optimization. Keep the already
                # authorized deterministic RRF order when the local model
                # is explicitly unavailable; unexpected errors propagate.
                fused = rerank_candidates
            finally:
                observe("netra_retrieval_provider_duration_seconds", time.perf_counter() - stage, provider="reranker")
            fused = fused[:self.settings.rerank_top_k]
        fused = fused[:min(query.top_k, self.settings.final_evidence_max)]
        stage = time.perf_counter()
        try:
            with stage_span(self.tracer, "retrieval.evidence_check", operation="evidence_resolution") as span:
                resolutions = await self.evidence_resolver.resolve(
                    auth, [hit.evidence_id for hit in fused],
                    allowed_source_version_ids=query.source_version_ids, require_active=require_active)
                if len(resolutions) != len(fused):
                    # The resolver contract is one outcome per id, in order.
                    # A mismatch would pair hits with the wrong verdicts.
                    raise RuntimeError("evidence resolver returned a misaligned result")
                accepted = [hit for hit, resolution in zip(fused, resolutions) if resolution.is_resolved]
                span.set(**{"netra.evidence_ids": [hit.evidence_id for hit in accepted][:16],
                            "netra.evidence_count": len(accepted),
                            "netra.rejected_count": len(fused) - len(accepted)})
        except Exception:
            increment("netra_retrieval_failures_total")
            raise
        observe("netra_retrieval_provider_duration_seconds", time.perf_counter() - stage, provider="canonical_resolution")
        result = [RetrievalHit(evidence_id=hit.evidence_id, score=hit.score) for hit in accepted]
        observe("netra_retrieval_duration_seconds", time.perf_counter() - started)
        log_event(logging.getLogger(__name__), "retrieval_completed", component="retrieval",
                  duration_ms=round((time.perf_counter() - started) * 1000, 3))
        return result
