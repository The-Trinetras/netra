"""Canonical evidence resolver for search-chunk and derived-video IDs.

A search chunk resolves as SOURCE_VERIFIED: it is text lifted from the source
itself. A video evidence candidate resolves as DERIVED: a provider model wrote
that description, so it is never interchangeable with the source's own words.
The same authorization, version-pin, status and active checks apply to both.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from netra_api.content.sources.models import SourceVersionStatus
from netra_api.content.retrieval.evidence import Evidence, EvidenceRejectionReason, EvidenceResolution, EvidenceTrust
from netra_api.db.models import (ReadingBlockRow, SearchChunkRow, SourceRow, SourceVersionRow,
                                 VideoEvidenceCandidateRow)
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
                # Not a chunk: it may be a derived video evidence candidate.
                # Same checks, different trust; misses still report NOT_FOUND.
                results.append(await self._resolve_video(auth, evidence_id, chunk_id,
                                                         pinned_source_version_id,
                                                         allowed_source_version_ids, require_active))
                continue
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
                evidence_id=evidence_id, source_version_id=version.source_version_id,
                evidence_version=version.version_number, locator=locator,
                text=chunk.text, provenance=f"source:{source.source_id};blocks:{','.join(chunk.block_ids)}",
                trust=EvidenceTrust.SOURCE_VERIFIED)))
        return results

    async def _resolve_video(self, auth: AuthContext, evidence_id: str, candidate_id: UUID,
                             pinned_source_version_id: UUID | None,
                             allowed_source_version_ids: list[UUID] | None,
                             require_active: bool) -> EvidenceResolution:
        """One derived video evidence candidate, or a rejection.

        Trust is DERIVED, never SOURCE_VERIFIED: the description is a provider
        model's account of the video, which is exactly what
        multimedia.evidence.resolve_and_authorize insists on before a
        multimedia tool may cite its own output.
        """

        def reject(reason: EvidenceRejectionReason) -> EvidenceResolution:
            return EvidenceResolution(evidence_id=evidence_id, rejection_reason=reason)

        row = (await self.session.execute(select(VideoEvidenceCandidateRow, SourceVersionRow, SourceRow)
            .join(SourceVersionRow,
                  SourceVersionRow.source_version_id == VideoEvidenceCandidateRow.source_version_id)
            .join(SourceRow, SourceRow.source_id == SourceVersionRow.source_id)
            .where(VideoEvidenceCandidateRow.candidate_id == candidate_id))).one_or_none()
        if row is None:
            return reject(EvidenceRejectionReason.NOT_FOUND)
        candidate, version, source = row
        if source.account_id != auth.account_id:
            return reject(EvidenceRejectionReason.UNAUTHORIZED)
        if allowed_source_version_ids is not None and version.source_version_id not in allowed_source_version_ids:
            return reject(EvidenceRejectionReason.SOURCE_VERSION_MISMATCH)
        if pinned_source_version_id is not None and version.source_version_id != pinned_source_version_id:
            return reject(EvidenceRejectionReason.SOURCE_VERSION_MISMATCH)
        if version.status != SourceVersionStatus.READY.value:
            return reject(EvidenceRejectionReason.SOURCE_VERSION_MISMATCH)
        if require_active and not version.is_active:
            return reject(EvidenceRejectionReason.SOURCE_VERSION_MISMATCH)
        return EvidenceResolution(evidence_id=evidence_id, evidence=Evidence(
            evidence_id=evidence_id, source_version_id=version.source_version_id,
            evidence_version=version.version_number, locator=candidate.locator,
            text=candidate.description,
            provenance=f"video:{candidate.video_id};{candidate.kind};{candidate.start_ms}-{candidate.end_ms}ms",
            trust=EvidenceTrust.DERIVED))
