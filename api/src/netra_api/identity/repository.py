"""Identity persistence interface. PostgreSQL is authoritative.

Every method returns None for an unknown record rather than raising, so the
service — not each repository implementation — decides that unknown and
unauthorized collapse into the same denial.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Protocol
from uuid import UUID

from netra_api.identity.access_codes import AccessCodeRecord
from netra_api.identity.models import Account, DeviceAccess, SessionBinding, StoredCredential


class IdentityRepository(Protocol):
    """Typed contract for reading identity and access records."""

    async def get_account(self, account_id: UUID) -> Optional[Account]:
        ...

    async def get_device(self, account_id: UUID, device_id: UUID) -> Optional[DeviceAccess]:
        ...

    async def find_credential(self, token_sha256: str) -> Optional[StoredCredential]:
        ...

    async def get_session_binding(self, session_id: UUID) -> Optional[SessionBinding]:
        ...

    async def create_session_binding(self, binding: SessionBinding) -> SessionBinding:
        """Record a new account–session binding. Raises if session_id is already bound."""
        ...

    async def issue_access_code(self, record: AccessCodeRecord) -> None:
        """Store a new code's digest, creating its account if it does not exist."""
        ...

    async def exchange_access_code(
        self, code_sha256: str, request_id: UUID, token_sha256: str, now: datetime
    ) -> Optional[datetime]:
        """Atomically apply access_codes.decide_exchange: on NEW create a device and a
        credential; on REPLAY revoke the code's previous credential and issue a new
        one on the same device. Returns the new credential's expiry, or None when
        the code is unknown, rejected or its account is inactive."""
        ...
