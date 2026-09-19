"""Operator-only credential provisioning. NOT a sign-in or issuance protocol.

Production credential issuance (the system-browser PKCE sign-in in
message-flow.md flow 1) and Windows credential storage remain open M1/M5
decisions. This tool exists so an operator can exercise the stored-credential
verifier against an explicitly chosen database (for example the disposable
integration database): it creates one active account, one device and one
bearer credential with an explicit expiry, stores only the credential's
SHA-256 digest, and prints the token once to stdout. The application never
imports it, and it has no default database or default expiry.

    NETRA_DATABASE_URL=postgresql+asyncpg://... python -m netra_api.identity.provisioning --expires-in-minutes 60
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


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--expires-in-minutes", type=int, required=True)
    parser.add_argument("--account-id", type=UUID, default=None)
    args = parser.parse_args(argv)

    from netra_api.config import Settings
    from netra_api.platform.database import create_engine

    url = Settings().database_url
    if not url:
        print("NETRA_DATABASE_URL is required", file=sys.stderr)
        return 2

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
