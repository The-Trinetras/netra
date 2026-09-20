"""M3 canonical video assets (closes the 0006 gap).

Revision ID: 0012_m3_video_assets
Revises: 0011_m1_speech_quota

Migration 0006 recorded: "``video_id`` has no foreign key: no canonical
video-asset table exists yet". This adds that table, so ``video_id`` finally
denotes a row Netra owns rather than a bare UUID minted by a job.

Mirrors ``netra_api.multimedia.video.models.VideoAsset`` column for column;
``api/tests/multimedia/test_video_asset_migration_shape.py`` fails if the two
drift.

The foreign keys on ``video_evidence_candidates.video_id`` and
``video_provider_bindings.video_id`` are deliberately NOT retrofitted here.
Those tables already exist on deployed databases and may hold rows whose
video_id has no asset; adding a constraint would fail the migration on exactly
the environments that matter. Backfill first, then constrain, in a later
reviewed migration.

Drafted covering M3; M2 reviews before this is applied anywhere but a
disposable local database.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0012_m3_video_assets"
down_revision = "0011_m1_speech_quota"
branch_labels = None
depends_on = None

_KINDS = "('upload', 'youtube')"


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)

    op.create_table(
        "video_assets",
        sa.Column("video_id", uuid, primary_key=True),
        sa.Column("source_id", uuid, sa.ForeignKey("sources.source_id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "source_version_id",
            uuid,
            sa.ForeignKey("source_versions.source_version_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        # The YouTube video id or the object-storage key; never a provider asset id.
        sa.Column("external_ref", sa.String(500)),
        # NULL means unknown duration, which time-range checks treat as
        # unbounded rather than as zero (VideoAsset.duration_ms).
        sa.Column("duration_ms", sa.Integer),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(f"kind IN {_KINDS}", name="ck_video_assets_kind"),
        sa.CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="ck_video_assets_duration"),
    )
    # One video per source version: a version is one piece of media, and this
    # is what asset_for_version() relies on to return a single row.
    op.create_index("ux_video_assets_source_version", "video_assets", ["source_version_id"], unique=True)
    op.create_index("ix_video_assets_source_id", "video_assets", ["source_id"])


def downgrade() -> None:
    op.drop_index("ix_video_assets_source_id", table_name="video_assets")
    op.drop_index("ux_video_assets_source_version", table_name="video_assets")
    op.drop_table("video_assets")
