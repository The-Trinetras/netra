"""API runtime configuration (environment variables prefixed ``NETRA_``).

Only values the repository has approved or that are pure wiring switches live
here. Unapproved product values — result-set retention, speech quota, total
binary frame size, credential issuance — have NO defaults: leaving them unset
disables the dependent capability explicitly instead of inventing policy.

Secrets are read from the environment by the process only; they are never
logged or returned by health endpoints.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NETRA_", extra="ignore")

    database_url: Optional[str] = None
    """postgresql+asyncpg URL for application access. Unset -> identity and
    session persistence are unavailable and every request fails closed."""

    auth_mode: Literal["unconfigured", "stored_credential"] = "unconfigured"
    """stored_credential verifies previously issued bearer credentials stored as
    SHA-256 digests (requires database_url). Issuance remains undecided."""

    result_set_ttl_seconds: Optional[int] = Field(default=None, ge=1)
    """Retention for stored result sets. Unapproved value: unset disables them."""

    trace_to_log: bool = True
