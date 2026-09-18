from __future__ import annotations

from typing import Protocol
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from netra_api.content.sources.models import SourceVersionStatus
from netra_api.db.models import SearchChunkRow, SourceRow, SourceVersionRow
from netra_api.platform.auth_context import AuthContext


class SearchCandidate(BaseModel):
    evidence_id: str
    score: float
    # Populated only after canonical evidence resolution when a reranker
    # needs passage text. Provider candidates must not supply trusted text.
    text: str | None = None


class ExactSearch(Protocol):
    async def search(self, auth: AuthContext, query_text: str, source_version_ids: list[UUID] | None,
                     top_k: int) -> list[SearchCandidate]: ...


class PostgresExactSearch:
    """PostgreSQL FTS reduced mode; scope is resolved before the query."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def search(self, auth: AuthContext, query_text: str, source_version_ids: list[UUID] | None,
                     top_k: int) -> list[SearchCandidate]:
        if not query_text.strip():
            return []
        vector = func.to_tsvector("simple", SearchChunkRow.text)
        tsquery = func.websearch_to_tsquery("simple", query_text)
        stmt = (select(SearchChunkRow.chunk_id, func.ts_rank_cd(vector, tsquery).label("score"))
                .join(SourceVersionRow, SourceVersionRow.source_version_id == SearchChunkRow.source_version_id)
                .join(SourceRow, SourceRow.source_id == SourceVersionRow.source_id)
                .where(SourceRow.account_id == auth.account_id, vector.op("@@")(tsquery)))
        if source_version_ids is not None:
            # Pinned scope: any completed version the caller named, including
            # one superseded by a newer activation.
            if not source_version_ids:
                return []
            stmt = stmt.where(SearchChunkRow.source_version_id.in_(source_version_ids),
                              SourceVersionRow.status == SourceVersionStatus.READY.value)
        else:
            stmt = stmt.where(SourceVersionRow.is_active.is_(True))
        rows = (await self.session.execute(stmt.order_by(desc("score"), SearchChunkRow.chunk_id)
                                           .limit(min(top_k, 20)))).all()
        return [SearchCandidate(evidence_id=str(row.chunk_id), score=float(row.score)) for row in rows]
