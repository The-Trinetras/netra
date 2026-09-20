"""Atomic application boundary for creating a source and scheduling parsing."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from netra_api.content.sources.models import SourceVersionIngestionState, SourceVersionStatus
from netra_api.db.models import JobRow, OutboxRow, SourceRow, SourceVersionRow
from netra_api.db.transactions import close_read_only_transaction
from netra_api.platform.auth_context import AuthContext
from netra_api.content.telemetry import log_event
from netra_api.platform.errors import AuthorizationError


from netra_api.db.outbox import SOURCE_VERSION_INGESTION_REQUESTED  # noqa: E402  (single definition)


def source_id_for(operation_key: str) -> UUID:
    """The source id one operation key resolves to.

    Exposed so a caller can tell a first create from a replay without
    duplicating the derivation string.
    """

    return uuid5(NAMESPACE_URL, f"netra:source:{operation_key}")


class SourceIngestionCreation(BaseModel):
    source_id: UUID
    source_version_id: UUID
    parse_job_id: UUID
    outbox_event_id: UUID


class SourceIngestionService:
    """Create canonical source state and its first durable work atomically.

    ``operation_key`` is the caller's replay-safe application idempotency key.
    IDs are derived from the account and operation key so a replay can rebuild
    the same source/version identity without a second idempotency table. The
    operation key is global to the application workflow, not account-scoped;
    reusing it from another account therefore resolves to the original source
    and is rejected by the canonical ownership check.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_source_and_schedule_parse(
        self,
        auth: AuthContext,
        title: str,
        object_key: str,
        content_type: str,
        content_hash: str,
        *,
        parser_name: str = "pymupdf",
        parser_version: str = "1.28.2",
        parser_config: dict | None = None,
        operation_key: str,
    ) -> SourceIngestionCreation:
        self._validate(title, object_key, content_type, content_hash, operation_key)
        source_id = source_id_for(operation_key)
        version_id = uuid5(source_id, "version:1")
        parse_key = f"parse_document:{version_id}"
        parsed_key = f"_netra/parsed/{version_id}.json"
        now = datetime.now(timezone.utc)

        await close_read_only_transaction(self.session)
        async with self.session.begin():
            existing = (await self.session.execute(
                select(JobRow).where(JobRow.operation_key == parse_key)
            )).scalar_one_or_none()
            if existing is not None:
                source = (await self.session.execute(
                    select(SourceRow).where(SourceRow.source_id == source_id,
                                             SourceRow.account_id == auth.account_id)
                )).scalar_one_or_none()
                if source is None:
                    raise AuthorizationError("source ingestion operation is not accessible")
                event = (await self.session.execute(
                    select(OutboxRow).where(OutboxRow.aggregate_id == version_id,
                                            OutboxRow.event_type == SOURCE_VERSION_INGESTION_REQUESTED)
                    .order_by(OutboxRow.created_at).limit(1)
                )).scalar_one_or_none()
                if event is None:
                    raise RuntimeError("parse job exists without its ingestion outbox event")
                return SourceIngestionCreation(source_id=source_id, source_version_id=version_id,
                                               parse_job_id=existing.job_id, outbox_event_id=event.event_id)

            source = SourceRow(source_id=source_id, account_id=auth.account_id, title=title, created_at=now)
            version = SourceVersionRow(
                source_version_id=version_id, source_id=source_id, version_number=1,
                status=SourceVersionStatus.PENDING.value, is_active=False, created_at=now,
                object_key=object_key, content_hash=content_hash, parser_name=parser_name,
                parser_version=parser_version, parser_config=parser_config or {},
                ingestion_state=SourceVersionIngestionState.PENDING.value, completed_stages=[],
            )
            payload = {
                "idempotency_key": parse_key, "source_id": str(source_id),
                "source_version_id": str(version_id), "object_key": object_key,
                "content_type": content_type, "parsed_object_key": parsed_key,
            }
            job = JobRow(
                job_id=uuid5(version_id, "job:parse_document"), job_type="parse_document",
                status="pending", attempts=0, max_attempts=5, next_run_at=now,
                operation_key=parse_key, payload=payload, completed_stages=[],
                remote_operation_ids={}, created_at=now, updated_at=now,
            )
            event = OutboxRow(
                event_id=uuid5(version_id, "outbox:source_version_ingestion_requested"),
                aggregate_type="source_version", aggregate_id=version_id,
                event_type=SOURCE_VERSION_INGESTION_REQUESTED,
                payload={"source_id": str(source_id), "source_version_id": str(version_id),
                         "operation_key": parse_key, "job_type": "parse_document",
                         "object_key": object_key, "content_type": content_type,
                         "parsed_object_key": parsed_key},
                created_at=now, attempt_count=0,
            )
            self.session.add_all([source, version, job, event])
        log_event(logging.getLogger(__name__), "source_created", component="ingestion",
                  source_id=source_id, source_version_id=version_id)
        log_event(logging.getLogger(__name__), "source_version_created", component="ingestion",
                  source_id=source_id, source_version_id=version_id, operation_key=parse_key)
        return SourceIngestionCreation(source_id=source_id, source_version_id=version_id,
                                       parse_job_id=job.job_id, outbox_event_id=event.event_id)

    @staticmethod
    def _validate(title: str, object_key: str, content_type: str,
                  content_hash: str, operation_key: str) -> None:
        if not title.strip() or not object_key.strip() or not operation_key.strip():
            raise ValueError("title, object_key, and operation_key are required")
        if content_type != "application/pdf":
            raise ValueError("source ingestion currently requires application/pdf")
        if len(content_hash) != 64 or content_hash != content_hash.lower():
            raise ValueError("content_hash must be a lowercase SHA-256 hex digest")
        try:
            int(content_hash, 16)
        except ValueError as exc:
            raise ValueError("content_hash must be a lowercase SHA-256 hex digest") from exc
