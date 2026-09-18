"""Credential extraction and verification for HTTP and the WebSocket upgrade.

PROPOSED presentation (needs M5 review; no committed contract defines it):
``Authorization: Bearer <token>`` on each HTTP request and on the WSS upgrade
request. Query-string tokens are deliberately not accepted, so credentials do
not land in URLs, proxy logs or history.

Verification is delegated to the configured CredentialVerifier, which fails
closed when none is configured.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from netra_api.identity.service import MAX_CREDENTIAL_CHARS, CredentialVerifier
from netra_api.platform.auth_context import AuthenticatedPrincipal

_BEARER = "bearer "


def extract_bearer(headers: Mapping[str, Any]) -> Optional[str]:
    """Return the bearer token from an Authorization header, or None.

    Header lookup is case-insensitive; a malformed or oversized header yields
    None so the verifier reports AUTH_REQUIRED uniformly.
    """

    value = None
    getter = getattr(headers, "get", None)
    if getter is not None:
        value = getter("authorization") or getter("Authorization")
    if not isinstance(value, str) or not value.lower().startswith(_BEARER):
        return None
    token = value[len(_BEARER):].strip()
    if not token or len(token) > MAX_CREDENTIAL_CHARS or any(ch.isspace() for ch in token):
        return None
    return token


async def authenticate(verifier: CredentialVerifier, headers: Mapping[str, Any]) -> AuthenticatedPrincipal:
    return await verifier.verify(extract_bearer(headers))
