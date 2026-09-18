"""Identity persistence interface. PostgreSQL is authoritative.

Every method returns None for an unknown record rather than raising, so the
service — not each repository implementation — decides that unknown and
unauthorized collapse into the same denial.
"""

from __future__ import annotations

from typing import Optional, Protocol
from uuid import UUID

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
