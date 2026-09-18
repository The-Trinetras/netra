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


class IdempotencyConflictError(NetraError):
    """Raised when a request_id is reused for a different logical action.

    A retransmission of the SAME action must replay its prior result
    regardless of payload identity; a request_id reused for a DIFFERENT
    payload is a client bug (or a collision) and must fail closed rather
    than execute either interpretation or overwrite the original replay
    record (see the idempotent-retry-ordering execution clarification).
    """

    def __init__(self, request_id: str) -> None:
        self.request_id = request_id
        super().__init__(f"request_id {request_id} was already used for a different request")


class TurnBudgetExceededError(NetraError):
    """Raised when a Coordinator turn exceeds its model-decision, tool-call, or deadline budget."""


class UnsupportedProtocolVersionError(NetraError):
    """Raised when a message declares a protocol_version this server does not support."""


class AuthenticationRequiredError(NetraError):
    """Raised when no valid credential accompanies a request.

    Distinct from AuthorizationError: the caller is not yet anyone, so the
    client must sign in again rather than being told it lacks access.
    """


class InvalidRequestError(NetraError):
    """Raised when an inbound message or tool/model input fails validation.

    Carries only a safe field name (never validator internals) so a wire
    error can say which field was wrong without leaking parser detail.
    """

    def __init__(self, message: str, *, field: str | None = None) -> None:
        self.field = field
        super().__init__(message)


class StaleRequestError(NetraError):
    """Raised when a request targets output or references that are no longer current.

    Examples: acknowledging a cancelled generation, opening an expired
    result set, or a turn whose originating deadline already passed.
    """


class ResourceUnavailableError(NetraError):
    """Raised when a required application dependency is not registered or reachable.

    Unavailable dependencies fail explicitly; they never degrade into a
    fabricated success (CLAUDE.md: unimplemented persistence fails closed).
    """


class ProviderUnavailableError(NetraError):
    """Raised when an external provider adapter is unconfigured or failing."""


class TurnCancelledError(NetraError):
    """Raised inside a turn when STOP, supersession or disconnect cancelled it.

    Never converted into closing speech: cancellation ends output silently.
    """


class ErrorCode:
    """Namespace of wire-safe error codes for the server_to_client `error` payload.

    Each code maps to exactly one NetraError subclass (or an
    authentication/internal condition with no dedicated exception yet)
    so the client can distinguish failure kinds without ever seeing an
    exception message, a stack trace, or database detail. See
    docs/architecture/message-flow.md and shared/contracts/protocol/v1/error.schema.json.
    """

    AUTH_REQUIRED = "AUTH_REQUIRED"
    AUTHORIZATION_DENIED = "AUTHORIZATION_DENIED"
    SESSION_VERSION_CONFLICT = "SESSION_VERSION_CONFLICT"
    REQUEST_ID_CONFLICT = "REQUEST_ID_CONFLICT"
    INVALID_REQUEST = "INVALID_REQUEST"
    UNSUPPORTED_PROTOCOL_VERSION = "UNSUPPORTED_PROTOCOL_VERSION"
    STALE_REQUEST = "STALE_REQUEST"
    RESOURCE_UNAVAILABLE = "RESOURCE_UNAVAILABLE"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


_RETRYABLE_CODES = frozenset(
    {
        ErrorCode.RESOURCE_UNAVAILABLE,
        ErrorCode.PROVIDER_UNAVAILABLE,
    }
)

_CODE_BY_EXCEPTION: dict[type[NetraError], str] = {
    AuthorizationError: ErrorCode.AUTHORIZATION_DENIED,
    SessionVersionConflictError: ErrorCode.SESSION_VERSION_CONFLICT,
    IdempotencyConflictError: ErrorCode.REQUEST_ID_CONFLICT,
    DuplicateRequestError: ErrorCode.REQUEST_ID_CONFLICT,
    UnsupportedProtocolVersionError: ErrorCode.UNSUPPORTED_PROTOCOL_VERSION,
    AuthenticationRequiredError: ErrorCode.AUTH_REQUIRED,
    InvalidRequestError: ErrorCode.INVALID_REQUEST,
    StaleRequestError: ErrorCode.STALE_REQUEST,
    TurnCancelledError: ErrorCode.STALE_REQUEST,
    ResourceUnavailableError: ErrorCode.RESOURCE_UNAVAILABLE,
    ProviderUnavailableError: ErrorCode.PROVIDER_UNAVAILABLE,
}
"""AUTHORIZATION_DENIED deliberately covers both "not found" and "not
authorized": the evidence-resolution boundary (content/retrieval/evidence.py)
already treats those as distinct *internal* rejection reasons that must
never reach the wire, because telling them apart externally would let a
caller probe for the existence of a resource they cannot access."""


def error_code_for(exc: NetraError) -> str:
    """Map a NetraError to its wire ErrorCode, defaulting to INTERNAL_ERROR.

    Never derives the code from exc's message text: only the exception's
    *type* selects a code, so nothing exception-specific (a stack trace,
    a database detail, a provider payload) can leak into the wire value.
    """

    for exc_type, code in _CODE_BY_EXCEPTION.items():
        if isinstance(exc, exc_type):
            return code
    return ErrorCode.INTERNAL_ERROR


def is_retryable(code: str) -> bool:
    """Whether a client may usefully resend the same request_id for this code.

    A version conflict or a request-id conflict is not retryable as-is:
    resending the identical payload would fail the same way. The caller
    must first reconcile (fetch a snapshot, mint a new request_id) before
    trying again.
    """

    return code in _RETRYABLE_CODES
