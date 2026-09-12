"""Coordinator guardrails.

Encodes the "The Coordinator must not" rules from CLAUDE.md. Only rules
that can be checked mechanically and unambiguously at this boilerplate
stage are implemented as runtime guards; the rest are structural — the
Coordinator is simply never given a tool that could do them — and are
listed below for reference rather than re-implemented as heuristics.
"""

from __future__ import annotations

from uuid import UUID

from netra_api.platform.auth_context import AuthContext


def assert_within_account_scope(auth: AuthContext, target_account_id: UUID) -> None:
    """The Coordinator must not access another account's data."""

    auth.assert_owns_account(target_account_id)


STRUCTURALLY_ENFORCED_RESTRICTIONS = (
    "no arbitrary SQL execution",
    "no direct mutation of learning mastery",
    "no bypassing authorization",
    "no inventing evidence bodies",
    "no shell command execution from model output",
)
"""Enforced by omission: the ToolRegistry (coordinator/tool_registry.py) is
never given a tool that could do these things, so no runtime check exists
here for them. Revisit if a future tool could plausibly violate one."""
