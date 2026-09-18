"""SQLAlchemy mappings for M2-owned canonical and runtime records.

These mappings are persistence details; callers should use the domain models
and repository interfaces rather than exposing ORM instances.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, text, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def uuid_column() -> Mapped[UUID]:
    return mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)


class SourceRow(Base):
    __tablename__ = "sources"

    source_id: Mapped[UUID] = uuid_column()
    account_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    versions: Mapped[list[SourceVersionRow]] = relationship(back_populates="source")


class SourceVersionRow(Base):
    __tablename__ = "source_versions"
    __table_args__ = (
        UniqueConstraint("source_id", "version_number", name="uq_source_version_number"),
        Index("ix_source_versions_source_active", "source_id", "is_active"),
    )

    source_version_id: Mapped[UUID] = uuid_column()
    source_id: Mapped[UUID] = mapped_column(ForeignKey("sources.source_id", ondelete="CASCADE"), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    object_key: Mapped[str | None] = mapped_column(String(1000))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    parser_name: Mapped[str | None] = mapped_column(String(200))
    parser_version: Mapped[str | None] = mapped_column(String(100))
    parser_config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    ingestion_state: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    completed_stages: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    source: Mapped[SourceRow] = relationship(back_populates="versions")


class ReadingBlockRow(Base):
    __tablename__ = "reading_blocks"
    __table_args__ = (
        UniqueConstraint("source_version_id", "sequence_id", name="uq_reading_block_sequence"),
        Index("ix_reading_blocks_version_sequence", "source_version_id", "sequence_id"),
    )

    block_id: Mapped[UUID] = uuid_column()
    source_version_id: Mapped[UUID] = mapped_column(ForeignKey("source_versions.source_version_id", ondelete="CASCADE"), nullable=False)
    sequence_id: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    page_index: Mapped[int | None] = mapped_column(Integer)
    printed_page: Mapped[str | None] = mapped_column(String(100))
    structured_location: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    sentences: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)


class SearchChunkRow(Base):
    __tablename__ = "search_chunks"
    __table_args__ = (Index("ix_search_chunks_version", "source_version_id"),)

    chunk_id: Mapped[UUID] = uuid_column()
    source_version_id: Mapped[UUID] = mapped_column(ForeignKey("source_versions.source_version_id", ondelete="CASCADE"), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    block_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    embedding_version: Mapped[str] = mapped_column(String(200), nullable=False)
    retrieval_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    embedding: Mapped[list[float] | None] = mapped_column(JSONB)
    embedding_spec: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class JobRow(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("operation_key", name="uq_jobs_operation_key"),
        Index("ix_jobs_claimable", "status", "next_run_at", "lease_until"),
    )

    job_id: Mapped[UUID] = uuid_column()
    job_type: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    worker_id: Mapped[str | None] = mapped_column(String(200))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    operation_key: Mapped[str] = mapped_column(String(500), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    completed_stages: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    remote_operation_ids: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False, default=dict)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OutboxRow(Base):
    __tablename__ = "outbox"
    __table_args__ = (
        Index("ix_outbox_claimable", "created_at",
              postgresql_where=text("processed_at IS NULL AND dead_lettered_at IS NULL")),
    )

    event_id: Mapped[UUID] = uuid_column()
    aggregate_type: Mapped[str] = mapped_column(String(100), nullable=False)
    aggregate_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(200), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    claim_token: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    claim_worker_id: Mapped[str | None] = mapped_column(String(200))
    claim_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dead_lettered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(64))


class MultimediaCandidateRow(Base):
    """One M3 extraction candidate (figure/chart/diagram/equation/table).

    Stored with its source-check verdict and findings. ``citable`` is the
    producing job's explicit decision; storage never upgrades it, and nothing
    here registers DERIVED evidence (that stays with the API service layer).
    """

    __tablename__ = "multimedia_candidates"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_multimedia_candidates_idempotency_key"),
        Index("ix_multimedia_candidates_version_kind", "source_version_id", "kind"),
        CheckConstraint("kind IN ('figure', 'chart', 'diagram', 'equation', 'table')",
                        name="ck_multimedia_candidates_kind"),
        CheckConstraint("object_index IS NULL OR object_index >= 0",
                        name="ck_multimedia_candidates_object_index"),
    )

    candidate_id: Mapped[UUID] = uuid_column()
    idempotency_key: Mapped[str] = mapped_column(String(500), nullable=False)
    source_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("source_versions.source_version_id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    object_index: Mapped[int | None] = mapped_column(Integer)
    object_ref: Mapped[str | None] = mapped_column(String(200))
    structure: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    validation: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    findings: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    citable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class VideoEvidenceCandidateRow(Base):
    """One derived, not-yet-citable video evidence candidate (M3 producer)."""

    __tablename__ = "video_evidence_candidates"
    __table_args__ = (
        UniqueConstraint("idempotency_key", "ordinal", name="uq_video_evidence_candidates_batch"),
        Index("ix_video_evidence_candidates_video", "video_id", "start_ms"),
        CheckConstraint("start_ms >= 0 AND end_ms >= start_ms", name="ck_video_evidence_candidates_range"),
    )

    candidate_id: Mapped[UUID] = uuid_column()
    idempotency_key: Mapped[str] = mapped_column(String(500), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    video_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    source_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("source_versions.source_version_id", ondelete="CASCADE"), nullable=False)
    locator: Mapped[str] = mapped_column(String(500), nullable=False)
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class VideoProviderBindingRow(Base):
    """Which provider asset backs a canonical Netra video (never the reverse)."""

    __tablename__ = "video_provider_bindings"

    video_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    provider: Mapped[str] = mapped_column(String(100), primary_key=True)
    provider_index_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    provider_video_id: Mapped[str] = mapped_column(String(200), nullable=False)
    model_name: Mapped[str] = mapped_column(String(200), nullable=False)
    model_version: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
