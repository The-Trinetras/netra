"""Operator-only provisioning: student access codes (D-CRED) and test credentials.

Issue a one-time access code for a student (decision D-CRED). The desktop app
exchanges it once at POST /v1/device-credentials. Only the code's digest is
stored; the code is printed once to stdout. Both lifetimes are required:

    NETRA_DATABASE_URL=postgresql+asyncpg://... python -m netra_api.identity.provisioning \
        --access-code --code-valid-days 7 --credential-valid-days 120 [--account-id <uuid>]

Without --account-id a new account is created; pass the same account id to
give a student a replacement code. A direct bearer credential for exercising
the verifier against a disposable database (not for students):

    NETRA_DATABASE_URL=postgresql+asyncpg://... python -m netra_api.identity.provisioning --expires-in-minutes 60

The application never imports this module, and it has no default database.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import secrets
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Sequence
from uuid import UUID, uuid4

from sqlalchemy import insert

from netra_api.identity.access_codes import (
    MAX_CODE_VALIDITY,
    MAX_CREDENTIAL_LIFETIME,
    AccessCodeRecord,
    access_code_digest,
    generate_access_code,
)
from netra_api.identity.postgres import account_credentials, account_devices, accounts
from netra_api.identity.service import credential_digest

MAX_EXPIRY = timedelta(days=7)


@dataclass(frozen=True)
class ProvisionedCredential:
    token: str
    account_id: UUID
    device_id: UUID
    expires_at: datetime


async def provision(engine: Any, *, expires_in: timedelta, account_id: Optional[UUID] = None) -> ProvisionedCredential:
    """Create (or reuse) an active account plus one device-bound credential, atomically."""

    if not timedelta(0) < expires_in <= MAX_EXPIRY:
        raise ValueError("expiry must be positive and at most seven days")
    now = datetime.now(timezone.utc)
    token = secrets.token_urlsafe(32)
    account_id = account_id or uuid4()
    device_id = uuid4()
    async with engine.begin() as connection:
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        await connection.execute(pg_insert(accounts).values(account_id=account_id, is_active=True)
                                 .on_conflict_do_nothing(index_elements=["account_id"]))
        await connection.execute(insert(account_devices).values(device_id=device_id, account_id=account_id))
        await connection.execute(insert(account_credentials).values(
            credential_id=uuid4(), token_sha256=credential_digest(token), account_id=account_id,
            device_id=device_id, issued_at=now, expires_at=now + expires_in))
    return ProvisionedCredential(token=token, account_id=account_id, device_id=device_id, expires_at=now + expires_in)


@dataclass(frozen=True)
class IssuedAccessCode:
    code: str
    account_id: UUID
    expires_at: datetime
    credential_lifetime: timedelta


async def issue_access_code(
    repository: Any,
    *,
    code_valid_for: timedelta,
    credential_lifetime: timedelta,
    account_id: Optional[UUID] = None,
    now: Optional[datetime] = None,
) -> IssuedAccessCode:
    """Store a new one-time code for a student account (created if new); return the code once."""

    if not timedelta(0) < code_valid_for <= MAX_CODE_VALIDITY:
        raise ValueError("code validity must be positive and at most 30 days")
    if not timedelta(seconds=1) <= credential_lifetime <= MAX_CREDENTIAL_LIFETIME:
        raise ValueError("credential lifetime must be positive and at most 180 days")
    now = now or datetime.now(timezone.utc)
    code = generate_access_code()
    record = AccessCodeRecord(
        code_id=uuid4(), code_sha256=access_code_digest(code), account_id=account_id or uuid4(),
        issued_at=now, expires_at=now + code_valid_for,
        credential_lifetime_seconds=int(credential_lifetime.total_seconds()),
    )
    await repository.issue_access_code(record)
    return IssuedAccessCode(code=code, account_id=record.account_id, expires_at=record.expires_at,
                            credential_lifetime=credential_lifetime)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--access-code", action="store_true", help="issue a one-time student access code")
    parser.add_argument("--code-valid-days", type=int)
    parser.add_argument("--credential-valid-days", type=int)
    parser.add_argument("--expires-in-minutes", type=int)
    parser.add_argument("--account-id", type=UUID, default=None)
    args = parser.parse_args(argv)
    if args.access_code and (args.code_valid_days is None or args.credential_valid_days is None):
        parser.error("--access-code needs --code-valid-days and --credential-valid-days")
    if not args.access_code and args.expires_in_minutes is None:
        parser.error("--expires-in-minutes is required (or use --access-code)")

    from netra_api.config import Settings
    from netra_api.platform.database import create_engine

    url = Settings().database_url
    if not url:
        print("NETRA_DATABASE_URL is required", file=sys.stderr)
        return 2

    if args.access_code:
        from netra_api.identity.postgres import PostgresIdentityRepository

        async def run_code() -> IssuedAccessCode:
            engine = create_engine(url)
            try:
                return await issue_access_code(
                    PostgresIdentityRepository(engine), code_valid_for=timedelta(days=args.code_valid_days),
                    credential_lifetime=timedelta(days=args.credential_valid_days), account_id=args.account_id)
            finally:
                await engine.dispose()

        code = asyncio.run(run_code())
        print(json.dumps({"access_code": code.code, "account_id": str(code.account_id),
                          "code_expires_at": code.expires_at.isoformat(),
                          "credential_valid_days": code.credential_lifetime.days}))
        return 0

    async def run() -> ProvisionedCredential:
        engine = create_engine(url)
        try:
            return await provision(engine, expires_in=timedelta(minutes=args.expires_in_minutes),
                                   account_id=args.account_id)
        finally:
            await engine.dispose()

    issued = asyncio.run(run())
    print(json.dumps({"token": issued.token, "account_id": str(issued.account_id),
                      "device_id": str(issued.device_id), "expires_at": issued.expires_at.isoformat()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
