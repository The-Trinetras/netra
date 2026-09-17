"""Unauthenticated liveness only.

Reports that the process is serving requests and which integrations are
registered, as booleans. It never reports provider credentials, database
URLs, identifiers or error text, and it is not a readiness claim that any
dependency is reachable.
"""

from __future__ import annotations

from typing import Any


def liveness(registered: dict[str, bool]) -> dict[str, Any]:
    return {"status": "ok", "registered": {name: bool(value) for name, value in sorted(registered.items())}}
