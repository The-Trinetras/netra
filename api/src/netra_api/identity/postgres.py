"""PostgreSQL identity repository (SQLAlchemy Core over postgresql+asyncpg).

Table shapes below are PROPOSED for M2's reviewed Alembic migration; this
module never creates or alters schema. Until that migration is reviewed and
applied, queries fail and the transport reports RESOURCE_UNAVAILABLE rather
than granting access.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, Table, and_, insert, select
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.exc import IntegrityError

from netra_api.identity.models import Account, DeviceAccess, SessionBinding, StoredCredential
from netra_api.platform.database import M1_METADATA
from netra_api.platform.errors import IdempotencyConflictError

accounts = Table(
    "accounts",
    M1_METADATA,
    Column("account_id", PGUUID(as_uuid=True), primary_key=True),
    Column("is_active", Boolean, nullable=False),
)

account_devices = Table(
    "account_devices",
    M1_METADATA,
    Column("device_id", PGUUID(as_uuid=True), primary_key=True),
    Column("account_id", PGUUID(as_uuid=True), ForeignKey("accounts.account_id"), nullable=False, index=True),
    Column("revoked_at", DateTime(timezone=True), nullable=True),
)

account_credentials = Table(
    "account_credentials",
    M1_METADATA,
    Column("credential_id", PGUUID(as_uuid=True), primary_key=True),
    Column("token_sha256", String(64), nullable=False, unique=True),
    Column("account_id", PGUUID(as_uuid=True), ForeignKey("accounts.account_id"), nullable=False),
    Column("device_id", PGUUID(as_uuid=True), ForeignKey("account_devices.device_id"), nullable=True),
    Column("issued_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("revoked_at", DateTime(timezone=True), nullable=True),
)

session_bindings = Table(
    "session_bindings",
    M1_METADATA,
    Column("session_id", PGUUID(as_uuid=True), primary_key=True),
    Column("account_id", PGUUID(as_uuid=True), ForeignKey("accounts.account_id"), nullable=False, index=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("revoked_at", DateTime(timezone=True), nullable=True),
)


class PostgresIdentityRepository:
    def __init__(self, engine) -> None:
        self._engine = engine

    async def get_account(self, account_id: UUID) -> Optional[Account]:
        async with self._engine.connect() as connection:
            row = (await connection.execute(select(accounts).where(accounts.c.account_id == account_id))).mappings().first()
        return Account.model_validate(dict(row)) if row else None

    async def get_device(self, account_id: UUID, device_id: UUID) -> Optional[DeviceAccess]:
        statement = select(account_devices).where(
            and_(account_devices.c.device_id == device_id, account_devices.c.account_id == account_id)
        )
        async with self._engine.connect() as connection:
            row = (await connection.execute(statement)).mappings().first()
        return DeviceAccess.model_validate(dict(row)) if row else None

    async def find_credential(self, token_sha256: str) -> Optional[StoredCredential]:
        statement = select(account_credentials).where(account_credentials.c.token_sha256 == token_sha256)
        async with self._engine.connect() as connection:
            row = (await connection.execute(statement)).mappings().first()
        return StoredCredential.model_validate(dict(row)) if row else None

    async def get_session_binding(self, session_id: UUID) -> Optional[SessionBinding]:
        statement = select(session_bindings).where(session_bindings.c.session_id == session_id)
        async with self._engine.connect() as connection:
            row = (await connection.execute(statement)).mappings().first()
        return SessionBinding.model_validate(dict(row)) if row else None

    async def create_session_binding(self, binding: SessionBinding) -> SessionBinding:
        try:
            async with self._engine.begin() as connection:
                await connection.execute(insert(session_bindings).values(**binding.model_dump()))
        except IntegrityError as exc:
            raise IdempotencyConflictError(str(binding.session_id)) from exc
        return binding
