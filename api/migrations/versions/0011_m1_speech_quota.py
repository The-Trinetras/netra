"""D-QUOTA daily speech characters per account.

Revision ID: 0011_m1_speech_quota
Revises: 0010_m1_turn_budgets

Mirrors ``speech_quota_usage`` in ``netra_api.speech.postgres`` column for
column; ``api/tests/platform/test_m1_migration_shape.py`` fails if the two
drift. Drafted by M1 (A4); M2 reviews.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0011_m1_speech_quota"
down_revision = "0010_m1_turn_budgets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "speech_quota_usage",
        sa.Column("account_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("usage_day", sa.Date, primary_key=True),
        sa.Column("characters_used", sa.Integer, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("speech_quota_usage")
