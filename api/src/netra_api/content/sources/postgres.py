"""Async PostgreSQL implementation of the source repository contract."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import re
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from netra_api.content.sources.models import (Source, SourceVersion, SourceVersionIngestionState,
                                               SourceVersionStatus)
from netra_api.db.models import SourceRow, SourceVersionRow
from netra_api.db.transactions import close_read_only_transaction
from netra_api.platform.auth_context import AuthContext
from netra_api.platform.errors import AuthorizationError, NetraError


class SourceVersionConflictError(NetraError):
    """The source changed since the caller read its active version."""


class SourceVersionIdentityError(NetraError):
    """An immutable source-version identity was missing or changed."""


_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_STAGES = ("parsing", "blocks_built", "embedded", "projected")
_STAGE_STATES = {
    "parsing": SourceVersionIngestionState.PARSING,
    "blocks_built": SourceVersionIngestionState.BLOCKS_BUILT,
    "embedded": SourceVersionIngestionState.EMBEDDED,
    "projected": SourceVersionIngestionState.PROJECTED,
}


def _source(row: SourceRow) -> Source:
    return Source(source_id=row.source_id, account_id=row.account_id, title=row.title, created_at=row.created_at)


def _version(row: SourceVersionRow) -> SourceVersion:
    return SourceVersion(source_version_id=row.source_version_id, source_id=row.source_id,
                         version_number=row.version_number, status=row.status, is_active=row.is_active,
                         created_at=row.created_at, activated_at=row.activated_at,
                         object_key=row.object_key, content_hash=row.content_hash,
                         parser_name=row.parser_name, parser_version=row.parser_version,
                         parser_config=row.parser_config or {}, ingestion_state=row.ingestion_state,
                         completed_stages=row.completed_stages or [])


class AsyncSourceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _close_read_transaction(self) -> None:
        """Close SQLAlchemy's read autobegin before a stage transaction.

        Worker stages read a version, may perform external work, and then
        update it in a separate short transaction.  The read itself starts
        SQLAlchemy's autobegin transaction, so it must be closed before
        ``session.begin()`` is entered. Only a read-only transaction is
        closed; another operation's pending writes raise
        ``ForeignTransactionError`` instead of being committed.
        """
        await close_read_only_transaction(self.session)

    async def create_source(self, auth: AuthContext, title: str) -> Source:
        row = SourceRow(source_id=uuid4(), account_id=auth.account_id, title=title,
                        created_at=datetime.now(timezone.utc))
        await self._close_read_transaction()
        async with self.session.begin():
            self.session.add(row)
        return _source(row)

    async def get_source(self, auth: AuthContext, source_id: UUID) -> Source:
        row = (await self.session.execute(select(SourceRow).where(SourceRow.source_id == source_id))).scalar_one_or_none()
        if row is None or row.account_id != auth.account_id:
            raise AuthorizationError("source is not accessible")
        return _source(row)

    async def list_sources(self, auth: AuthContext) -> list[Source]:
        rows = (await self.session.execute(select(SourceRow).where(SourceRow.account_id == auth.account_id)
                                          .order_by(SourceRow.created_at, SourceRow.source_id))).scalars()
        return [_source(row) for row in rows]

    async def list_versions(self, auth: AuthContext, source_id: UUID) -> list[SourceVersion]:
        await self.get_source(auth, source_id)
        rows = (await self.session.execute(select(SourceVersionRow).where(SourceVersionRow.source_id == source_id)
                                          .order_by(SourceVersionRow.version_number))).scalars()
        return [_version(row) for row in rows]

    async def get_version(self, auth: AuthContext, source_version_id: UUID) -> SourceVersion:
        row = (await self.session.execute(select(SourceVersionRow).join(SourceRow)
                                          .where(SourceVersionRow.source_version_id == source_version_id,
                                                 SourceRow.account_id == auth.account_id))).scalar_one_or_none()
        if row is None:
            raise AuthorizationError("source version is not accessible")
        return _version(row)

    async def get_version_internal(self, source_version_id: UUID) -> SourceVersion:
        row = (await self.session.execute(select(SourceVersionRow).where(
            SourceVersionRow.source_version_id == source_version_id))).scalar_one_or_none()
        if row is None:
            raise NetraError("source version does not exist")
        return _version(row)

    async def mark_stage_complete_internal(self, source_version_id: UUID, stage: str) -> SourceVersion:
        if stage not in _STAGES:
            raise ValueError("unknown ingestion stage")
        await self._close_read_transaction()
        async with self.session.begin():
            row = (await self.session.execute(select(SourceVersionRow).where(
                SourceVersionRow.source_version_id == source_version_id).with_for_update())).scalar_one_or_none()
            if row is None:
                raise NetraError("source version does not exist")
            # A replay of an earlier stage must never regress a version that
            # has already crossed the readiness or activation gate.
            if stage in (row.completed_stages or []) and row.ingestion_state in {
                    SourceVersionIngestionState.READY.value,
                    SourceVersionIngestionState.ACTIVE.value}:
                return _version(row)
            stages = list(row.completed_stages or [])
            if stage not in stages:
                required_before = _STAGES[:_STAGES.index(stage)]
                if any(previous not in stages for previous in required_before):
                    raise NetraError("ingestion stages must complete in order")
                stages.append(stage)
            row.completed_stages = stages
            row.ingestion_state = _STAGE_STATES[stage].value
            row.status = SourceVersionStatus.PROCESSING.value
        return _version(row)

    async def mark_ready_internal(self, source_version_id: UUID) -> SourceVersion:
        """Close the canonical projection gate for a trusted worker.

        This is deliberately separate from activation: a projected version
        becomes READY, but only the activation transaction can make it ACTIVE.
        The lock and complete-stage checks make duplicate delivery harmless.
        """
        await self._close_read_transaction()
        async with self.session.begin():
            row = (await self.session.execute(select(SourceVersionRow).where(
                SourceVersionRow.source_version_id == source_version_id).with_for_update())).scalar_one_or_none()
            if row is None:
                raise NetraError("source version does not exist")
            if row.ingestion_state == SourceVersionIngestionState.ACTIVE.value:
                return _version(row)
            if row.ingestion_state == SourceVersionIngestionState.READY.value:
                return _version(row)
            if set(row.completed_stages or []) != set(_STAGES):
                raise NetraError("all ingestion stages must complete before ready")
            if not all((row.object_key, row.content_hash, row.parser_name, row.parser_version)):
                raise SourceVersionIdentityError("complete source-version identity is required before ready")
            row.ingestion_state = SourceVersionIngestionState.READY.value
            row.status = SourceVersionStatus.READY.value
        return _version(row)

    async def mark_failed_internal(self, source_version_id: UUID) -> SourceVersion:
        await self._close_read_transaction()
        async with self.session.begin():
            row = (await self.session.execute(select(SourceVersionRow).where(
                SourceVersionRow.source_version_id == source_version_id).with_for_update())).scalar_one_or_none()
            if row is None:
                raise NetraError("source version does not exist")
            row.ingestion_state = SourceVersionIngestionState.FAILED.value
            row.status = SourceVersionStatus.FAILED.value
        return _version(row)

    async def active_version_number_internal(self, source_id: UUID) -> int:
        """Trusted worker read: the active version number, or 0 when none is active."""
        number = (await self.session.execute(select(SourceVersionRow.version_number).where(
            SourceVersionRow.source_id == source_id, SourceVersionRow.is_active.is_(True)))).scalar_one_or_none()
        return number or 0

    async def get_active_version(self, auth: AuthContext, source_id: UUID) -> SourceVersion | None:
        await self.get_source(auth, source_id)
        row = (await self.session.execute(select(SourceVersionRow).where(SourceVersionRow.source_id == source_id,
                                                                          SourceVersionRow.is_active.is_(True)))).scalar_one_or_none()
        return _version(row) if row else None

    async def create_version(self, auth: AuthContext, source_id: UUID, *, object_key: str | None = None,
                             content_hash: str | None = None, parser_name: str | None = None,
                             parser_version: str | None = None, parser_config: dict | None = None) -> SourceVersion:
        self._validate_identity(object_key, content_hash, parser_name, parser_version)
        await self._close_read_transaction()
        async with self.session.begin():
            locked = (await self.session.execute(select(SourceRow).where(SourceRow.source_id == source_id,
                                                                         SourceRow.account_id == auth.account_id)
                                                .with_for_update())).scalar_one_or_none()
            if locked is None:
                raise AuthorizationError("source is not accessible")
            latest = (await self.session.execute(select(SourceVersionRow.version_number)
                                                .where(SourceVersionRow.source_id == locked.source_id)
                                                .order_by(SourceVersionRow.version_number.desc()).limit(1))).scalar_one_or_none()
            row = SourceVersionRow(source_version_id=uuid4(), source_id=source_id,
                                   version_number=(latest or 0) + 1, status=SourceVersionStatus.PENDING.value,
                                   is_active=False, created_at=datetime.now(timezone.utc),
                                   object_key=object_key, content_hash=content_hash,
                                   parser_name=parser_name, parser_version=parser_version,
                                   parser_config=parser_config or {}, ingestion_state=SourceVersionIngestionState.PENDING.value,
                                   completed_stages=[])
            self.session.add(row)
        return _version(row)

    @staticmethod
    def _validate_identity(object_key: str | None, content_hash: str | None,
                           parser_name: str | None, parser_version: str | None) -> None:
        if content_hash is not None and not _HASH_RE.fullmatch(content_hash):
            raise SourceVersionIdentityError("content_hash must be a lowercase SHA-256 hex digest")
        if any(value is not None and not value.strip() for value in (object_key, parser_name, parser_version)):
            raise SourceVersionIdentityError("source-version identity values must not be blank")

    @staticmethod
    def content_hash_for(data: bytes) -> str:
        """Canonical source-byte identity: lowercase SHA-256."""
        return hashlib.sha256(data).hexdigest()

    async def mark_stage_complete(self, auth: AuthContext, source_version_id: UUID, stage: str) -> SourceVersion:
        if stage not in _STAGES:
            raise ValueError("unknown ingestion stage")
        await self._close_read_transaction()
        async with self.session.begin():
            row = await self._owned_version(auth, source_version_id, lock=True)
            stages = list(row.completed_stages or [])
            if stage not in stages:
                required_before = _STAGES[:_STAGES.index(stage)]
                if any(previous not in stages for previous in required_before):
                    raise NetraError("ingestion stages must complete in order")
                stages.append(stage)
            row.completed_stages = stages
            row.ingestion_state = _STAGE_STATES[stage].value
            row.status = SourceVersionStatus.PROCESSING.value
        return _version(row)

    async def set_ingestion_identity(self, auth: AuthContext, source_version_id: UUID, *, object_key: str,
                                     content_hash: str, parser_name: str, parser_version: str,
                                     parser_config: dict | None = None) -> SourceVersion:
        self._validate_identity(object_key, content_hash, parser_name, parser_version)
        await self._close_read_transaction()
        async with self.session.begin():
            row = await self._owned_version(auth, source_version_id, lock=True)
            proposed = (object_key, content_hash, parser_name, parser_version, parser_config or {})
            existing = (row.object_key, row.content_hash, row.parser_name, row.parser_version, row.parser_config or {})
            if any(value is not None for value in existing[:4]) and existing != proposed:
                raise SourceVersionIdentityError("source-version identity is immutable")
            row.object_key, row.content_hash, row.parser_name = object_key, content_hash, parser_name
            row.parser_version, row.parser_config = parser_version, parser_config or {}
        return _version(row)

    async def get_version_by_content_hash(self, auth: AuthContext, source_id: UUID,
                                          content_hash: str) -> SourceVersion | None:
        await self.get_source(auth, source_id)
        self._validate_identity(None, content_hash, None, None)
        row = (await self.session.execute(select(SourceVersionRow).where(
            SourceVersionRow.source_id == source_id, SourceVersionRow.content_hash == content_hash))).scalar_one_or_none()
        return _version(row) if row else None

    async def mark_ready(self, auth: AuthContext, source_version_id: UUID) -> SourceVersion:
        await self._close_read_transaction()
        async with self.session.begin():
            row = await self._owned_version(auth, source_version_id, lock=True)
            if set(row.completed_stages or []) != set(_STAGES):
                raise NetraError("all ingestion stages must complete before ready")
            if not all((row.object_key, row.content_hash, row.parser_name, row.parser_version)):
                raise SourceVersionIdentityError("complete source-version identity is required before ready")
            row.ingestion_state = SourceVersionIngestionState.READY.value
            row.status = SourceVersionStatus.READY.value
        return _version(row)

    async def mark_failed(self, auth: AuthContext, source_version_id: UUID) -> SourceVersion:
        await self._close_read_transaction()
        async with self.session.begin():
            row = await self._owned_version(auth, source_version_id, lock=True)
            row.ingestion_state = SourceVersionIngestionState.FAILED.value
            row.status = SourceVersionStatus.FAILED.value
        return _version(row)

    async def _owned_version(self, auth: AuthContext, source_version_id: UUID, *, lock: bool = False) -> SourceVersionRow:
        statement = (select(SourceVersionRow).join(SourceRow)
                     .where(SourceVersionRow.source_version_id == source_version_id,
                            SourceRow.account_id == auth.account_id))
        if lock:
            statement = statement.with_for_update()
        row = (await self.session.execute(statement)).scalar_one_or_none()
        if row is None:
            raise AuthorizationError("source version is not accessible")
        return row

    async def activate_version(self, auth: AuthContext, source_id: UUID, source_version_id: UUID,
                               expected_version_number: int) -> SourceVersion:
        return await self._activate_version(source_id, source_version_id, expected_version_number,
                                            account_id=auth.account_id)

    async def activate_version_internal(self, source_id: UUID, source_version_id: UUID,
                                        expected_version_number: int) -> SourceVersion:
        """Trusted worker path; canonical source ownership is checked in the transaction."""
        return await self._activate_version(source_id, source_version_id, expected_version_number)

    async def _activate_version(self, source_id: UUID, source_version_id: UUID,
                                expected_version_number: int, account_id: UUID | None = None) -> SourceVersion:
        # Repository reads use SQLAlchemy autobegin. Close a prior read-only
        # transaction before opening the short atomic activation transaction.
        await self._close_read_transaction()
        async with self.session.begin():
            source_query = select(SourceRow).where(SourceRow.source_id == source_id)
            if account_id is not None:
                source_query = source_query.where(SourceRow.account_id == account_id)
            source = (await self.session.execute(source_query.with_for_update())).scalar_one_or_none()
            if source is None:
                raise AuthorizationError("source is not accessible")
            row = (await self.session.execute(select(SourceVersionRow).where(
                SourceVersionRow.source_id == source_id, SourceVersionRow.source_version_id == source_version_id
            ).with_for_update())).scalar_one_or_none()
            if row is None:
                raise AuthorizationError("source version is not accessible")
            if row.is_active:
                # Redelivered activation of the version that is already
                # active: an idempotent success, not a version conflict.
                if row.ingestion_state != SourceVersionIngestionState.ACTIVE.value:
                    raise NetraError("active source version has invalid ingestion state")
                return _version(row)
            active = (await self.session.execute(select(SourceVersionRow).where(
                SourceVersionRow.source_id == source_id, SourceVersionRow.is_active.is_(True)
            ).with_for_update())).scalar_one_or_none()
            actual = active.version_number if active else 0
            if actual != expected_version_number:
                raise SourceVersionConflictError
            newest = (await self.session.execute(select(SourceVersionRow.version_number)
                         .where(SourceVersionRow.source_id == source_id)
                         .order_by(SourceVersionRow.version_number.desc()).limit(1))).scalar_one()
            if row.version_number != newest:
                raise SourceVersionConflictError("cannot activate an obsolete source version")
            if row.status != SourceVersionStatus.READY.value:
                raise NetraError("only a ready source version can be activated")
            if not set(_STAGES).issubset(row.completed_stages or []) or not all(
                    (row.object_key, row.content_hash, row.parser_name, row.parser_version)):
                raise NetraError("source version ingestion gates are incomplete")
            await self.session.execute(update(SourceVersionRow).where(SourceVersionRow.source_id == source_id,
                                                                       SourceVersionRow.source_version_id != source_version_id)
                                       .values(is_active=False, activated_at=None))
            row.is_active = True
            row.activated_at = datetime.now(timezone.utc)
            row.ingestion_state = SourceVersionIngestionState.ACTIVE.value
        return _version(row)
