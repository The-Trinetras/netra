"""Identity domain models.

PostgreSQL is authoritative for identity/session access (CLAUDE.md "Data
authority"). This module only defines the account shape the Coordinator
and session service depend on through AuthContext.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel


class Account(BaseModel):
    """Minimal authoritative account record."""

    account_id: UUID
    is_active: bool = True
