"""Identity domain models.

PostgreSQL is authoritative for identity and access (data-ownership.md):
Identity owns accounts, credential verification, device access and the
account–session security binding. Mutable reading/interaction state is the
Session service's, never Identity's.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class Account(BaseModel):
    """Minimal authoritative account record."""

    account_id: UUID
    is_active: bool = True


class DeviceAccess(BaseModel):
    """One device's access grant for an account.

    A revoked device loses access immediately, including on connections
    that were accepted before revocation: IdentityService re-checks this on
    every resolved context.
    """

    device_id: UUID
    account_id: UUID
    revoked_at: Optional[datetime] = None

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None


class StoredCredential(BaseModel):
    """A previously issued bearer credential, stored only as a digest.

    Issued by the D-CRED access-code exchange (identity/access_codes.py,
    POST /v1/device-credentials) or the operator provisioning tool. The raw
    token is never stored.
    """

    credential_id: UUID
    token_sha256: str = Field(min_length=64, max_length=64)
    account_id: UUID
    device_id: Optional[UUID] = None
    issued_at: datetime
    expires_at: datetime
    revoked_at: Optional[datetime] = None


class SessionBinding(BaseModel):
    """The account–session security binding Identity owns.

    Holding a session_id proves nothing; this record is what says which
    account may act on it. A revoked binding denies every account.
    """

    session_id: UUID
    account_id: UUID
    created_at: datetime
    revoked_at: Optional[datetime] = None
