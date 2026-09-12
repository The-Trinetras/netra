"""Shared exception hierarchy for Netra application services.

Coordinator, Tutor, and application services raise these instead of
leaking provider- or database-specific exception types across module
boundaries (see CLAUDE.md "Database boundaries" and "Hard execution limits").
"""

from __future__ import annotations


class NetraError(Exception):
    """Base class for all application-level errors."""


class AuthorizationError(NetraError):
    """Raised when an operation is attempted outside the caller's authorized account scope."""


class SessionVersionConflictError(NetraError):
    """Raised when a mutation's expected session version no longer matches the stored version."""

    def __init__(self, expected_version: int, actual_version: int) -> None:
        self.expected_version = expected_version
        self.actual_version = actual_version
        super().__init__(
            f"expected session version {expected_version}, but current version is {actual_version}"
        )


class DuplicateRequestError(NetraError):
    """Raised when a request_id has already been applied and must not be re-applied."""

    def __init__(self, request_id: str) -> None:
        self.request_id = request_id
        super().__init__(f"request_id {request_id} has already been processed")


class TurnBudgetExceededError(NetraError):
    """Raised when a Coordinator turn exceeds its model-decision, tool-call, or deadline budget."""


class UnsupportedProtocolVersionError(NetraError):
    """Raised when a message declares a protocol_version this server does not support."""
