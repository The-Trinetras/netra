"""M3 multimedia candidate storage (INT-04).

Revision ID: 0006_m3_multimedia_candidates
Revises: 0005_m1_identity_session

Backs M3's ExtractionCandidateSink / TableCandidateSink (multimedia_candidates),
VideoEvidenceSink (video_evidence_candidates) and ProviderBindingSink
(video_provider_bindings). M3 owns extraction semantics; M2 owns storage.

``video_id`` has no foreign key: no canonical video-asset table exists yet
(recorded as an M3/M1 decision in docs/team/handoffs/M2.md). Candidates are
tied to an existing source version and cascade with it.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0006_m3_multimedia_candidates"
down_revision = "0005_m1_identity_session"
branch_labels = None
depends_on = None

_KINDS = "('figure', 'chart', 'diagram', 'equation', 'table')"


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    jsonb = postgresql.JSONB()
    tz = sa.DateTime(timezone=True)

    def version_fk() -> sa.ForeignKey:
        # A ForeignKey object can belong to one column only; build one per table.
        return sa.ForeignKey("source_versions.source_version_id", ondelete="CASCADE")

    op.create_table(
        "multimedia_candidates",
        sa.Column("candidate_id", uuid, primary_key=True),
        sa.Column("idempotency_key", sa.String(500), nullable=False),
        sa.Column("source_version_id", uuid, version_fk(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("object_index", sa.Integer),
        sa.Column("object_ref", sa.String(200)),
        sa.Column("structure", jsonb, nullable=False),
        sa.Column("validation", jsonb, nullable=False),
        sa.Column("findings", jsonb, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("citable", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", tz, nullable=False),
        sa.UniqueConstraint("idempotency_key", name="uq_multimedia_candidates_idempotency_key"),
        sa.CheckConstraint(f"kind IN {_KINDS}", name="ck_multimedia_candidates_kind"),
        sa.CheckConstraint("object_index IS NULL OR object_index >= 0",
                           name="ck_multimedia_candidates_object_index"),
    )
    op.create_index("ix_multimedia_candidates_version_kind", "multimedia_candidates",
                    ["source_version_id", "kind"])

    op.create_table(
        "video_evidence_candidates",
        sa.Column("candidate_id", uuid, primary_key=True),
        sa.Column("idempotency_key", sa.String(500), nullable=False),
        sa.Column("ordinal", sa.Integer, nullable=False),
        sa.Column("video_id", uuid, nullable=False),
        sa.Column("source_version_id", uuid, version_fk(), nullable=False),
        sa.Column("locator", sa.String(500), nullable=False),
        sa.Column("start_ms", sa.Integer, nullable=False),
        sa.Column("end_ms", sa.Integer, nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("provenance", jsonb, nullable=False),
        sa.Column("created_at", tz, nullable=False),
        sa.UniqueConstraint("idempotency_key", "ordinal", name="uq_video_evidence_candidates_batch"),
        sa.CheckConstraint("start_ms >= 0 AND end_ms >= start_ms",
                           name="ck_video_evidence_candidates_range"),
    )
    op.create_index("ix_video_evidence_candidates_video", "video_evidence_candidates",
                    ["video_id", "start_ms"])

    op.create_table(
        "video_provider_bindings",
        sa.Column("video_id", uuid, primary_key=True),
        sa.Column("provider", sa.String(100), primary_key=True),
        sa.Column("provider_index_id", sa.String(200), primary_key=True),
        sa.Column("provider_video_id", sa.String(200), nullable=False),
        sa.Column("model_name", sa.String(200), nullable=False),
        sa.Column("model_version", sa.String(100), nullable=False),
        sa.Column("created_at", tz, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("video_provider_bindings")
    op.drop_index("ix_video_evidence_candidates_video", table_name="video_evidence_candidates")
    op.drop_table("video_evidence_candidates")
    op.drop_index("ix_multimedia_candidates_version_kind", table_name="multimedia_candidates")
    op.drop_table("multimedia_candidates")
