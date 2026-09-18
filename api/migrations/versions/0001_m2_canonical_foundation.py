"""M2 canonical content, jobs and outbox foundation.

Revision ID: 0001_m2_canonical_foundation
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_m2_canonical_foundation"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    jsonb = postgresql.JSONB()
    op.create_table("sources",
        sa.Column("source_id", uuid, primary_key=True),
        sa.Column("account_id", uuid, nullable=False), sa.Column("title", sa.String(500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_sources_account_id", "sources", ["account_id"])
    op.create_table("source_versions",
        sa.Column("source_version_id", uuid, primary_key=True),
        sa.Column("source_id", uuid, sa.ForeignKey("sources.source_id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_number", sa.Integer, nullable=False), sa.Column("status", sa.String(32), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("source_id", "version_number", name="uq_source_version_number"))
    op.create_index("ix_source_versions_source_active", "source_versions", ["source_id", "is_active"])
    op.create_index("uq_source_versions_one_active", "source_versions", ["source_id"], unique=True,
                    postgresql_where=sa.text("is_active = true"))
    op.create_table("reading_blocks",
        sa.Column("block_id", uuid, primary_key=True),
        sa.Column("source_version_id", uuid, sa.ForeignKey("source_versions.source_version_id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence_id", sa.Integer, nullable=False), sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("text", sa.Text, nullable=False, server_default=""), sa.Column("page_index", sa.Integer),
        sa.Column("printed_page", sa.String(100)), sa.Column("structured_location", jsonb, nullable=False),
        sa.Column("sentences", jsonb, nullable=False),
        sa.UniqueConstraint("source_version_id", "sequence_id", name="uq_reading_block_sequence"))
    op.create_index("ix_reading_blocks_version_sequence", "reading_blocks", ["source_version_id", "sequence_id"])
    op.create_table("search_chunks",
        sa.Column("chunk_id", uuid, primary_key=True),
        sa.Column("source_version_id", uuid, sa.ForeignKey("source_versions.source_version_id", ondelete="CASCADE"), nullable=False),
        sa.Column("text", sa.Text, nullable=False), sa.Column("block_ids", jsonb, nullable=False),
        sa.Column("embedding_version", sa.String(200), nullable=False), sa.Column("metadata", jsonb, nullable=False))
    op.create_index("ix_search_chunks_version", "search_chunks", ["source_version_id"])
    op.create_index("ix_search_chunks_fts", "search_chunks", [sa.text("to_tsvector('simple', text)")],
                    postgresql_using="gin")
    op.create_table("jobs",
        sa.Column("job_id", uuid, primary_key=True), sa.Column("job_type", sa.String(200), nullable=False),
        sa.Column("status", sa.String(32), nullable=False), sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("lease_token", uuid), sa.Column("worker_id", sa.String(200)), sa.Column("attempts", sa.Integer, nullable=False),
        sa.Column("max_attempts", sa.Integer, nullable=False), sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("operation_key", sa.String(500), nullable=False), sa.Column("payload", jsonb, nullable=False),
        sa.Column("completed_stages", jsonb, nullable=False), sa.Column("remote_operation_ids", jsonb, nullable=False),
        sa.Column("last_error", sa.Text), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("operation_key", name="uq_jobs_operation_key"))
    op.create_index("ix_jobs_claimable", "jobs", ["status", "next_run_at", "lease_until"])
    op.create_table("outbox",
        sa.Column("event_id", uuid, primary_key=True), sa.Column("aggregate_type", sa.String(100), nullable=False),
        sa.Column("aggregate_id", uuid, nullable=False), sa.Column("event_type", sa.String(200), nullable=False),
        sa.Column("payload", jsonb, nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True)), sa.Column("attempt_count", sa.Integer, nullable=False))
    op.create_index("ix_outbox_pending", "outbox", ["processed_at", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_search_chunks_fts", table_name="search_chunks")
    for table in ("outbox", "jobs", "search_chunks", "reading_blocks", "source_versions", "sources"):
        op.drop_table(table)
