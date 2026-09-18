"""Persist canonical embedding results for the D2 worker pipeline."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003_d2_embeddings"
down_revision = "0002_d1_ingestion_identity_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("search_chunks", sa.Column("embedding", postgresql.JSONB()))
    op.add_column("search_chunks", sa.Column("embedding_spec", postgresql.JSONB()))


def downgrade() -> None:
    op.drop_column("search_chunks", "embedding_spec")
    op.drop_column("search_chunks", "embedding")
