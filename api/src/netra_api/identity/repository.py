"""Identity persistence interface. PostgreSQL is authoritative; no DB access here."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from netra_api.identity.models import Account


class IdentityRepository(Protocol):
    """Typed contract for reading account records."""

    def get_account(self, account_id: UUID) -> Account:
        ...
