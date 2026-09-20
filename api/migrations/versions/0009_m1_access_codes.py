"""D-CRED one-time access codes.

Revision ID: 0009_m1_access_codes
Revises: 0008_learning_questions_attempts

Mirrors ``access_codes`` in ``netra_api.identity.postgres`` column for column;
``api/tests/platform/test_m1_migration_shape.py`` fails if the two drift.
Only a SHA-256 digest of each code is stored. Drafted by M1 (A2); M2 reviews.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0009_m1_access_codes"
down_revision = "0008_learning_questions_attempts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    tz = sa.DateTime(timezone=True)

    op.create_table(
        "access_codes",
        sa.Column("code_id", uuid, primary_key=True),
        sa.Column("code_sha256", sa.String(64), nullable=False, unique=True),
        sa.Column("account_id", uuid, sa.ForeignKey("accounts.account_id"), nullable=False),
        sa.Column("issued_at", tz, nullable=False),
        sa.Column("expires_at", tz, nullable=False),
        sa.Column("credential_lifetime_seconds", sa.Integer, nullable=False),
        sa.Column("exchanged_at", tz, nullable=True),
        sa.Column("exchange_request_id", uuid, nullable=True),
        sa.Column("credential_id", uuid, sa.ForeignKey("account_credentials.credential_id"), nullable=True),
        sa.Column("revoked_at", tz, nullable=True),
    )
    op.create_index("ix_access_codes_account_id", "access_codes", ["account_id"])


def downgrade() -> None:
    op.drop_index("ix_access_codes_account_id", table_name="access_codes")
    op.drop_table("access_codes")
