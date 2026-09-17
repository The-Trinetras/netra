"""Add immutable source-version ingestion identity and stage state."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002_d1_ingestion_identity_state"
down_revision = "0001_m2_canonical_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("source_versions", sa.Column("object_key", sa.String(1000)))
    op.add_column("source_versions", sa.Column("content_hash", sa.String(64)))
    op.add_column("source_versions", sa.Column("parser_name", sa.String(200)))
    op.add_column("source_versions", sa.Column("parser_version", sa.String(100)))
    op.add_column("source_versions", sa.Column("parser_config", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")))
    op.add_column("source_versions", sa.Column("ingestion_state", sa.String(32), nullable=False, server_default="pending"))
    op.add_column("source_versions", sa.Column("completed_stages", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")))
    op.create_index("uq_source_versions_content_hash", "source_versions", ["source_id", "content_hash"], unique=True,
                    postgresql_where=sa.text("content_hash IS NOT NULL"))
    op.execute(sa.text("""
        CREATE OR REPLACE FUNCTION netra_immutable_source_version_hash()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.content_hash IS NOT NULL AND OLD.content_hash IS DISTINCT FROM NEW.content_hash THEN
                RAISE EXCEPTION 'source version content_hash is immutable';
            END IF;
            RETURN NEW;
        END $$;
    """))
    op.execute(sa.text("""
        CREATE TRIGGER trg_source_version_hash_immutable
        BEFORE UPDATE ON source_versions
        FOR EACH ROW EXECUTE FUNCTION netra_immutable_source_version_hash();
    """))


def downgrade() -> None:
    op.execute(sa.text("DROP TRIGGER IF EXISTS trg_source_version_hash_immutable ON source_versions"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS netra_immutable_source_version_hash()"))
    op.drop_index("uq_source_versions_content_hash", table_name="source_versions")
    for name in ("completed_stages", "ingestion_state", "parser_config", "parser_version", "parser_name", "content_hash", "object_key"):
        op.drop_column("source_versions", name)
