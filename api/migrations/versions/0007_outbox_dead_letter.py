"""Outbox dead-letter marker and claimable index (INT-08).

Revision ID: 0007_outbox_dead_letter
Revises: 0006_m3_multimedia_candidates

Poison events (invalid payload, idempotency conflict, or an event type no
worker handles after bounded attempts) are marked dead-lettered with a safe
error code instead of being re-claimed forever. The event row is kept.
"""

from alembic import op
import sqlalchemy as sa

revision = "0007_outbox_dead_letter"
down_revision = "0006_m3_multimedia_candidates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("outbox", sa.Column("dead_lettered_at", sa.DateTime(timezone=True)))
    op.add_column("outbox", sa.Column("last_error_code", sa.String(64)))
    op.drop_index("ix_outbox_pending", table_name="outbox")
    op.create_index("ix_outbox_claimable", "outbox", ["created_at"],
                    postgresql_where=sa.text("processed_at IS NULL AND dead_lettered_at IS NULL"))


def downgrade() -> None:
    op.drop_index("ix_outbox_claimable", table_name="outbox")
    op.create_index("ix_outbox_pending", "outbox", ["processed_at", "claim_until", "created_at"])
    op.drop_column("outbox", "last_error_code")
    op.drop_column("outbox", "dead_lettered_at")
