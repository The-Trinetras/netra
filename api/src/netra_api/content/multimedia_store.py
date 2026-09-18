"""PostgreSQL storage behind M3's multimedia candidate sinks (INT-04).

M3 owns extraction/validation semantics and the sink Protocols
(``netra_api.multimedia.tables.service.TableCandidateSink`` and the worker
ports in ``netra_worker.jobs.multimedia``). M2 owns this storage:

- Every write is idempotent by the producer's ``idempotency_key``. A retry
  that re-runs extraction finds the first stored copy and changes nothing;
  the key reused for a *different* object is a conflict, never an overwrite.
- ``citable`` is stored exactly as the producing job decided. Storage never
  upgrades it, refuses ``citable=True`` without a passing source check, and
  never registers DERIVED evidence (that stays with the authorizing API
  service layer).
- Provider bindings keep provider asset/index IDs separate from canonical
  Netra IDs; a second, different binding for the same key is a conflict.
- Each call is one short transaction; no external call happens inside it.
- Reads are account-scoped through the owning source.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from netra_api.db.models import (
    MultimediaCandidateRow,
    SourceRow,
    SourceVersionRow,
    VideoEvidenceCandidateRow,
    VideoProviderBindingRow,
)
from netra_api.platform.auth_context import AuthContext
from netra_api.platform.errors import AuthorizationError, NetraError

CANDIDATE_KINDS = frozenset({"figure", "chart", "diagram", "equation", "table"})


class CandidateConflictError(NetraError):
    """An idempotency key or binding was reused for different content identity."""


class StoredCandidate(BaseModel):
    """Read model for a stored multimedia candidate (no ORM objects escape)."""

    candidate_id: UUID
    source_version_id: UUID
    kind: str
    object_index: Optional[int]
    object_ref: Optional[str]
    structure: dict[str, Any]
    validation: dict[str, Any]
    findings: list[dict[str, Any]]
    citable: bool
    created_at: datetime


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _require_key(idempotency_key: str) -> None:
    if not idempotency_key or not idempotency_key.strip() or len(idempotency_key) > 500:
        raise ValueError("idempotency_key must be a non-blank string of at most 500 characters")


class MultimediaCandidateStore:
    """Idempotent candidate/binding persistence; one short transaction per call."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def store_candidate(
        self,
        *,
        source_version_id: UUID,
        kind: str,
        structure: dict[str, Any],
        validation: dict[str, Any],
        findings: Iterable[dict[str, Any]] = (),
        citable: bool,
        verified: bool,
        idempotency_key: str,
        object_index: Optional[int] = None,
        object_ref: Optional[str] = None,
    ) -> UUID:
        """Persist one candidate and return its id (the existing id on replay).

        ``verified`` is the producer's source-check verdict. ``citable=True``
        without it is refused: storage must never make an unchecked
        extraction look authoritative.
        """

        _require_key(idempotency_key)
        if kind not in CANDIDATE_KINDS:
            raise ValueError(f"unknown multimedia candidate kind: {kind!r}")
        if object_index is not None and object_index < 0:
            raise ValueError("object_index must be >= 0")
        if citable and not verified:
            raise ValueError("a candidate cannot be citable without a passing source check")

        async with self._sessions() as session, session.begin():
            version = await session.get(SourceVersionRow, source_version_id)
            if version is None:
                raise NetraError("source version does not exist")
            inserted = (await session.execute(
                insert(MultimediaCandidateRow)
                .values(candidate_id=uuid4(), idempotency_key=idempotency_key,
                        source_version_id=source_version_id, kind=kind, object_index=object_index,
                        object_ref=object_ref, structure=structure, validation=validation,
                        findings=list(findings), citable=citable, created_at=_now())
                .on_conflict_do_nothing(index_elements=[MultimediaCandidateRow.idempotency_key])
                .returning(MultimediaCandidateRow.candidate_id)
            )).scalar_one_or_none()
            if inserted is not None:
                return inserted
            existing = (await session.execute(select(MultimediaCandidateRow).where(
                MultimediaCandidateRow.idempotency_key == idempotency_key))).scalar_one()
            identity = (existing.source_version_id, existing.kind, existing.object_index, existing.object_ref)
            if identity != (source_version_id, kind, object_index, object_ref):
                raise CandidateConflictError("idempotency key already stores a different object")
            return existing.candidate_id

    async def store_video_candidates(
        self,
        *,
        video_id: UUID,
        candidates: list[dict[str, Any]],
        idempotency_key: str,
    ) -> int:
        """Persist one batch of video candidates exactly once per key.

        Returns the number of rows stored under the key (existing rows on
        replay). Candidates are never citable here; each must belong to
        ``video_id`` and carry a valid time range.
        """

        _require_key(idempotency_key)
        for candidate in candidates:
            if candidate["video_id"] != video_id:
                raise ValueError("every candidate must belong to the requested video")
            if not 0 <= candidate["start_ms"] <= candidate["end_ms"]:
                raise ValueError("candidate time range is invalid")

        async with self._sessions() as session, session.begin():
            existing = (await session.execute(select(VideoEvidenceCandidateRow).where(
                VideoEvidenceCandidateRow.idempotency_key == idempotency_key))).scalars().all()
            if existing:
                if any(row.video_id != video_id for row in existing):
                    raise CandidateConflictError("idempotency key already stores another video's evidence")
                return len(existing)
            if not candidates:
                return 0
            now = _now()
            rows = [
                dict(candidate_id=uuid4(), idempotency_key=idempotency_key, ordinal=ordinal,
                     video_id=video_id, source_version_id=candidate["source_version_id"],
                     locator=candidate["locator"], start_ms=candidate["start_ms"],
                     end_ms=candidate["end_ms"], kind=candidate["kind"],
                     description=candidate["description"], provenance=candidate["provenance"],
                     created_at=now)
                for ordinal, candidate in enumerate(candidates)
            ]
            # A concurrent writer with the same key loses on the unique
            # (idempotency_key, ordinal) constraint instead of duplicating.
            result = await session.execute(
                insert(VideoEvidenceCandidateRow).values(rows)
                .on_conflict_do_nothing(constraint="uq_video_evidence_candidates_batch")
                .returning(VideoEvidenceCandidateRow.candidate_id))
            stored = len(result.scalars().all())
            if stored not in (0, len(rows)):
                raise CandidateConflictError("partially overlapping video candidate batch")
            return len(rows)

    async def bind_provider(
        self,
        *,
        video_id: UUID,
        provider: str,
        provider_index_id: str,
        provider_video_id: str,
        model_name: str,
        model_version: str,
    ) -> None:
        """Record which provider asset backs a canonical video; replay is a no-op."""

        values = (provider, provider_index_id, provider_video_id, model_name, model_version)
        if any(not value or not value.strip() for value in values):
            raise ValueError("provider binding fields must be non-blank")
        async with self._sessions() as session, session.begin():
            await session.execute(
                insert(VideoProviderBindingRow)
                .values(video_id=video_id, provider=provider, provider_index_id=provider_index_id,
                        provider_video_id=provider_video_id, model_name=model_name,
                        model_version=model_version, created_at=_now())
                .on_conflict_do_nothing(index_elements=[VideoProviderBindingRow.video_id,
                                                        VideoProviderBindingRow.provider,
                                                        VideoProviderBindingRow.provider_index_id]))
            row = await session.get(VideoProviderBindingRow, (video_id, provider, provider_index_id))
            if row is None:  # pragma: no cover - the insert or a prior row must exist
                raise NetraError("provider binding was not stored")
            if (row.provider_video_id, row.model_name, row.model_version) != (
                    provider_video_id, model_name, model_version):
                raise CandidateConflictError("video is already bound to a different provider asset or model")

    async def list_candidates(
        self,
        auth: AuthContext,
        source_version_id: UUID,
        kind: Optional[str] = None,
    ) -> list[StoredCandidate]:
        """Candidates for a source version the caller's account owns."""

        async with self._sessions() as session:
            owned = (await session.execute(
                select(SourceVersionRow.source_version_id).join(SourceRow)
                .where(SourceVersionRow.source_version_id == source_version_id,
                       SourceRow.account_id == auth.account_id))).scalar_one_or_none()
            if owned is None:
                raise AuthorizationError("source version is not accessible")
            query = select(MultimediaCandidateRow).where(
                MultimediaCandidateRow.source_version_id == source_version_id)
            if kind is not None:
                query = query.where(MultimediaCandidateRow.kind == kind)
            rows = (await session.execute(query.order_by(
                MultimediaCandidateRow.kind, MultimediaCandidateRow.object_index,
                MultimediaCandidateRow.created_at))).scalars().all()
        return [StoredCandidate(
            candidate_id=row.candidate_id, source_version_id=row.source_version_id, kind=row.kind,
            object_index=row.object_index, object_ref=row.object_ref, structure=row.structure,
            validation=row.validation, findings=row.findings, citable=row.citable,
            created_at=row.created_at) for row in rows]


class PostgresTableCandidateSink:
    """``TableCandidateSink`` (M3) backed by ``MultimediaCandidateStore``.

    Tables arriving here are never stored citable: per M3's contract the sink
    must not mark a table citable on its own. The validation report is stored
    so ``citable_tables`` can gate presentation on it.
    """

    def __init__(self, store: MultimediaCandidateStore) -> None:
        self._store = store

    async def store_candidate(self, source_version_id: UUID, table: Any, validation: Any,
                              idempotency_key: str) -> None:
        report = validation.model_dump(mode="json")
        report["is_source_verified"] = bool(validation.is_source_verified)
        await self._store.store_candidate(
            source_version_id=source_version_id,
            kind="table",
            object_ref=str(table.table_id),
            structure=table.model_dump(mode="json"),
            validation=report,
            findings=[item.model_dump(mode="json") for item in validation.findings],
            citable=False,
            verified=bool(validation.is_source_verified),
            idempotency_key=idempotency_key,
        )


__all__ = [
    "CANDIDATE_KINDS",
    "CandidateConflictError",
    "MultimediaCandidateStore",
    "PostgresTableCandidateSink",
    "StoredCandidate",
]
