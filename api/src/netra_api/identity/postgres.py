"""PostgreSQL identity repository (SQLAlchemy Core over postgresql+asyncpg).

Table shapes below are PROPOSED for M2's reviewed Alembic migration; this
module never creates or alters schema. Until that migration is reviewed and
applied, queries fail and the transport reports RESOURCE_UNAVAILABLE rather
than granting access.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Table, and_, insert, select, update
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from netra_api.identity.access_codes import AccessCodeRecord, ExchangeDecision, decide_exchange
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

access_codes = Table(
    "access_codes",
    M1_METADATA,
    Column("code_id", PGUUID(as_uuid=True), primary_key=True),
    Column("code_sha256", String(64), nullable=False, unique=True),
    Column("account_id", PGUUID(as_uuid=True), ForeignKey("accounts.account_id"), nullable=False, index=True),
    Column("issued_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("credential_lifetime_seconds", Integer, nullable=False),
    Column("exchanged_at", DateTime(timezone=True), nullable=True),
    Column("exchange_request_id", PGUUID(as_uuid=True), nullable=True),
    Column("credential_id", PGUUID(as_uuid=True), ForeignKey("account_credentials.credential_id"), nullable=True),
    Column("revoked_at", DateTime(timezone=True), nullable=True),
)
"""D-CRED one-time access codes (migration 0009); only the code's digest is stored."""


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

    async def issue_access_code(self, record: AccessCodeRecord) -> None:
        async with self._engine.begin() as connection:
            await connection.execute(pg_insert(accounts).values(account_id=record.account_id, is_active=True)
                                     .on_conflict_do_nothing(index_elements=["account_id"]))
            await connection.execute(insert(access_codes).values(**record.model_dump()))

    async def exchange_access_code(
        self, code_sha256: str, request_id: UUID, token_sha256: str, now: datetime
    ) -> Optional[datetime]:
        async with self._engine.begin() as connection:
            locked = select(access_codes).where(access_codes.c.code_sha256 == code_sha256).with_for_update()
            row = (await connection.execute(locked)).mappings().first()
            if row is None:
                return None
            record = AccessCodeRecord.model_validate(dict(row))
            active = (await connection.execute(
                select(accounts.c.is_active).where(accounts.c.account_id == record.account_id))).scalar()
            if not active:
                return None
            decision = decide_exchange(record, request_id, now)
            if decision is ExchangeDecision.REJECT:
                return None
            if decision is ExchangeDecision.REPLAY:
                device_id = (await connection.execute(
                    update(account_credentials).where(account_credentials.c.credential_id == record.credential_id)
                    .values(revoked_at=now).returning(account_credentials.c.device_id))).scalar_one()
            else:
                device_id = uuid4()
                await connection.execute(insert(account_devices).values(device_id=device_id, account_id=record.account_id))
            credential_id = uuid4()
            expires_at = now + timedelta(seconds=record.credential_lifetime_seconds)
            await connection.execute(insert(account_credentials).values(
                credential_id=credential_id, token_sha256=token_sha256, account_id=record.account_id,
                device_id=device_id, issued_at=now, expires_at=expires_at))
            await connection.execute(update(access_codes).where(access_codes.c.code_id == record.code_id).values(
                exchanged_at=record.exchanged_at or now, exchange_request_id=request_id, credential_id=credential_id))
            return expires_at
