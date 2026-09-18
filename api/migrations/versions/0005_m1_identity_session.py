"""M1 identity, session, replay, result-set and dialogue tables (INT-02).

Revision ID: 0005_m1_identity_session
Revises: 0004_d3_outbox_leases

M2 serializes this migration; M1 owns the record semantics. The tables mirror
the proposal in ``netra_api.platform.database.M1_METADATA`` (declared by
``identity/postgres.py`` and ``session/postgres.py``) column for column, with no
added constraints: foreign keys or types M1 did not declare are review
questions recorded in docs/team/handoffs/M2.md, not decisions taken here.
``api/tests/platform/test_m1_migration_shape.py`` fails if the two drift.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0005_m1_identity_session"
down_revision = "0004_d3_outbox_leases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    jsonb = postgresql.JSONB()
    tz = sa.DateTime(timezone=True)

    op.create_table(
        "accounts",
        sa.Column("account_id", uuid, primary_key=True),
        sa.Column("is_active", sa.Boolean, nullable=False),
    )
    op.create_table(
        "account_devices",
        sa.Column("device_id", uuid, primary_key=True),
        sa.Column("account_id", uuid, sa.ForeignKey("accounts.account_id"), nullable=False),
        sa.Column("revoked_at", tz, nullable=True),
    )
    op.create_index("ix_account_devices_account_id", "account_devices", ["account_id"])
    op.create_table(
        "account_credentials",
        sa.Column("credential_id", uuid, primary_key=True),
        sa.Column("token_sha256", sa.String(64), nullable=False, unique=True),
        sa.Column("account_id", uuid, sa.ForeignKey("accounts.account_id"), nullable=False),
        sa.Column("device_id", uuid, sa.ForeignKey("account_devices.device_id"), nullable=True),
        sa.Column("issued_at", tz, nullable=False),
        sa.Column("expires_at", tz, nullable=False),
        sa.Column("revoked_at", tz, nullable=True),
    )
    op.create_table(
        "session_bindings",
        sa.Column("session_id", uuid, primary_key=True),
        sa.Column("account_id", uuid, sa.ForeignKey("accounts.account_id"), nullable=False),
        sa.Column("created_at", tz, nullable=False),
        sa.Column("revoked_at", tz, nullable=True),
    )
    op.create_index("ix_session_bindings_account_id", "session_bindings", ["account_id"])
    op.create_table(
        "sessions",
        sa.Column("session_id", uuid, primary_key=True),
        sa.Column("account_id", uuid, nullable=False),
        sa.Column("session_version", sa.Integer, nullable=False),
        sa.Column("state", jsonb, nullable=False),
        sa.Column("updated_at", tz, nullable=False),
    )
    op.create_index("ix_sessions_account_id", "sessions", ["account_id"])
    op.create_table(
        "session_request_records",
        sa.Column("account_id", uuid, primary_key=True),
        sa.Column("request_id", uuid, primary_key=True),
        sa.Column("session_id", uuid, nullable=False),
        sa.Column("payload_fingerprint", sa.String(64), nullable=False),
        sa.Column("result", jsonb, nullable=False),
        sa.Column("created_at", tz, nullable=False),
    )
    op.create_index("ix_session_request_records_session_id", "session_request_records", ["session_id"])
    op.create_table(
        "session_result_sets",
        sa.Column("result_set_id", uuid, primary_key=True),
        sa.Column("session_id", uuid, nullable=False),
        sa.Column("source_version_id", sa.Text, nullable=False),
        sa.Column("created_at", tz, nullable=False),
        sa.Column("expires_at", tz, nullable=False),
        sa.Column("items", jsonb, nullable=False),
    )
    op.create_index("ix_session_result_sets_session_id", "session_result_sets", ["session_id"])
    op.create_table(
        "session_dialogue_entries",
        sa.Column("request_id", uuid, primary_key=True),
        sa.Column("ordinal", sa.Integer, primary_key=True),
        sa.Column("session_id", uuid, nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("created_at", tz, nullable=False),
    )
    op.create_index("ix_session_dialogue_entries_session_id", "session_dialogue_entries", ["session_id"])


def downgrade() -> None:
    for index, table in (
        ("ix_session_dialogue_entries_session_id", "session_dialogue_entries"),
        ("ix_session_result_sets_session_id", "session_result_sets"),
        ("ix_session_request_records_session_id", "session_request_records"),
        ("ix_sessions_account_id", "sessions"),
        ("ix_session_bindings_account_id", "session_bindings"),
        ("ix_account_devices_account_id", "account_devices"),
    ):
        op.drop_index(index, table_name=table)
    for table in (
        "session_dialogue_entries",
        "session_result_sets",
        "session_request_records",
        "sessions",
        "session_bindings",
        "account_credentials",
        "account_devices",
        "accounts",
    ):
        op.drop_table(table)
