"""Canonical evidence resolver for search-chunk IDs."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from netra_api.content.sources.models import SourceVersionStatus
from netra_api.content.retrieval.evidence import Evidence, EvidenceRejectionReason, EvidenceResolution, EvidenceTrust
from netra_api.db.models import ReadingBlockRow, SearchChunkRow, SourceRow, SourceVersionRow
from netra_api.platform.auth_context import AuthContext


class AsyncPostgresEvidenceResolver:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def resolve(self, auth: AuthContext, evidence_ids: list[str], pinned_source_version_id: UUID | None = None,
                      allowed_source_version_ids: list[UUID] | None = None,
                      require_active: bool = False) -> list[EvidenceResolution]:
        results: list[EvidenceResolution] = []
        for evidence_id in evidence_ids:
            try:
                chunk_id = UUID(evidence_id)
            except ValueError:
                results.append(EvidenceResolution(evidence_id=evidence_id, rejection_reason=EvidenceRejectionReason.NOT_FOUND))
                continue
            row = (await self.session.execute(select(SearchChunkRow, SourceVersionRow, SourceRow)
                .join(SourceVersionRow, SourceVersionRow.source_version_id == SearchChunkRow.source_version_id)
                .join(SourceRow, SourceRow.source_id == SourceVersionRow.source_id)
                .where(SearchChunkRow.chunk_id == chunk_id))).one_or_none()
            if row is None:
                results.append(EvidenceResolution(evidence_id=evidence_id, rejection_reason=EvidenceRejectionReason.NOT_FOUND)); continue
            chunk, version, source = row
            if source.account_id != auth.account_id:
                results.append(EvidenceResolution(evidence_id=evidence_id, rejection_reason=EvidenceRejectionReason.UNAUTHORIZED)); continue
            if allowed_source_version_ids is not None and version.source_version_id not in allowed_source_version_ids:
                results.append(EvidenceResolution(evidence_id=evidence_id, rejection_reason=EvidenceRejectionReason.SOURCE_VERSION_MISMATCH)); continue
            if pinned_source_version_id is not None and version.source_version_id != pinned_source_version_id:
                results.append(EvidenceResolution(evidence_id=evidence_id, rejection_reason=EvidenceRejectionReason.SOURCE_VERSION_MISMATCH)); continue
            if version.status != SourceVersionStatus.READY.value:
                # Pending, processing or failed ingestion is never citable.
                results.append(EvidenceResolution(evidence_id=evidence_id, rejection_reason=EvidenceRejectionReason.SOURCE_VERSION_MISMATCH)); continue
            if require_active and not version.is_active:
                results.append(EvidenceResolution(evidence_id=evidence_id, rejection_reason=EvidenceRejectionReason.SOURCE_VERSION_MISMATCH)); continue
            try:
                block_ids = {UUID(i) for i in chunk.block_ids}
            except (TypeError, ValueError):
                results.append(EvidenceResolution(evidence_id=evidence_id, rejection_reason=EvidenceRejectionReason.DELETED)); continue
            blocks = (await self.session.execute(select(ReadingBlockRow).where(
                ReadingBlockRow.source_version_id == version.source_version_id,
                ReadingBlockRow.block_id.in_(block_ids)).order_by(ReadingBlockRow.sequence_id))).scalars().all()
            if len(blocks) != len(block_ids) or len(block_ids) != len(chunk.block_ids):
                results.append(EvidenceResolution(evidence_id=evidence_id, rejection_reason=EvidenceRejectionReason.DELETED)); continue
            locator = ", ".join((b.structured_location or {}).get("locator", str(b.sequence_id)) for b in blocks)
            results.append(EvidenceResolution(evidence_id=evidence_id, evidence=Evidence(
                evidence_id=evidence_id, source_version_id=version.source_version_id, locator=locator,
                text=chunk.text, provenance=f"source:{source.source_id};blocks:{','.join(chunk.block_ids)}",
                trust=EvidenceTrust.SOURCE_VERIFIED)))
        return results
