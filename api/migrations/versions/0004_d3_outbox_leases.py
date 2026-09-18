"""Add safe reservation leases for pending outbox events."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004_d3_outbox_leases"
down_revision = "0003_d2_embeddings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("outbox", sa.Column("claim_token", postgresql.UUID(as_uuid=True)))
    op.add_column("outbox", sa.Column("claim_worker_id", sa.String(200)))
    op.add_column("outbox", sa.Column("claim_until", sa.DateTime(timezone=True)))
    op.drop_index("ix_outbox_pending", table_name="outbox")
    op.create_index("ix_outbox_pending", "outbox",
                    ["processed_at", "claim_until", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_outbox_pending", table_name="outbox")
    op.create_index("ix_outbox_pending", "outbox", ["processed_at", "created_at"])
    op.drop_column("outbox", "claim_until")
    op.drop_column("outbox", "claim_worker_id")
    op.drop_column("outbox", "claim_token")
