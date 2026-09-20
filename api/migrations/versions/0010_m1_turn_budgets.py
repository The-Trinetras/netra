"""D-BUDGET turn budget use per request id.

Revision ID: 0010_m1_turn_budgets
Revises: 0009_m1_access_codes

Mirrors ``turn_budgets`` in ``netra_api.session.postgres`` column for column;
``api/tests/platform/test_m1_migration_shape.py`` fails if the two drift.
Drafted by M1 (A3); M2 reviews. Rows are small and per turn; a retention
period is a pending product decision.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0010_m1_turn_budgets"
down_revision = "0009_m1_access_codes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    op.create_table(
        "turn_budgets",
        sa.Column("account_id", uuid, primary_key=True),
        sa.Column("request_id", uuid, primary_key=True),
        sa.Column("session_id", uuid, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("model_decisions_used", sa.Integer, nullable=False),
        sa.Column("tool_calls_used", sa.Integer, nullable=False),
        sa.Column("nested_model_calls", sa.Integer, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("turn_budgets")
