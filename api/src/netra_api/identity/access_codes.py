"""One-time access codes and the device-credential exchange shape (D-CRED).

Mirrors shared/contracts/http/v1/device_credential.schema.json. An operator
issues a student a code; the desktop app exchanges it once over HTTPS for a
device credential (POST /v1/device-credentials). Codes are 12 Crockford
base32 characters (about 60 bits), shown as three groups of four, and stored
only as a SHA-256 digest of the normalized form.
"""

from __future__ import annotations

import re
import secrets
from datetime import datetime, timedelta
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from netra_api.identity.service import credential_digest

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
"""Crockford base32: no I, L, O or U, so a code survives being read aloud."""
CODE_LENGTH = 12
REPLAY_WINDOW = timedelta(minutes=15)
"""A lost response may be retried with the same request_id for this long."""
MAX_CODE_VALIDITY = timedelta(days=30)
MAX_CREDENTIAL_LIFETIME = timedelta(days=180)

_CONFUSABLE = str.maketrans({"O": "0", "I": "1", "L": "1"})
_VALID = re.compile(f"[{ALPHABET}]{{{CODE_LENGTH}}}")


class ExchangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    access_code: str = Field(min_length=1, max_length=64)
    request_id: UUID


class CredentialIssued(BaseModel):
    model_config = ConfigDict(extra="forbid")

    credential: str = Field(min_length=32)
    expires_at: datetime


def generate_access_code() -> str:
    """A new random code in its display form, for example 7K3Q-M9TX-2BWD."""

    raw = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))
    return "-".join(raw[i : i + 4] for i in range(0, CODE_LENGTH, 4))


def normalize_access_code(typed: str) -> Optional[str]:
    """The canonical code for what a student typed, or None if it cannot be one.

    Ignores case, spaces and hyphens, and reads O as 0 and I or L as 1.
    """

    compact = re.sub(r"[\s-]", "", typed).upper().translate(_CONFUSABLE)
    return compact if _VALID.fullmatch(compact) else None


def access_code_digest(code: str) -> str:
    """SHA-256 of the normalized code; raises ValueError for a malformed code."""

    normalized = normalize_access_code(code)
    if normalized is None:
        raise ValueError("not an access code")
    return credential_digest(normalized)
