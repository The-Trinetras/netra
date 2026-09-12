"""Wire error codes must come from exception TYPE only, never message text."""

from netra_api.platform.errors import (
    AuthorizationError,
    DuplicateRequestError,
    ErrorCode,
    IdempotencyConflictError,
    NetraError,
    SessionVersionConflictError,
    UnsupportedProtocolVersionError,
    error_code_for,
    is_retryable,
)


def test_authorization_error_maps_to_authorization_denied():
    assert error_code_for(AuthorizationError("secret internal detail")) == ErrorCode.AUTHORIZATION_DENIED


def test_session_version_conflict_maps_to_its_own_code():
    assert error_code_for(SessionVersionConflictError(3, 5)) == ErrorCode.SESSION_VERSION_CONFLICT


def test_idempotency_conflict_maps_to_request_id_conflict():
    assert error_code_for(IdempotencyConflictError("req-1")) == ErrorCode.REQUEST_ID_CONFLICT


def test_duplicate_request_also_maps_to_request_id_conflict():
    assert error_code_for(DuplicateRequestError("req-1")) == ErrorCode.REQUEST_ID_CONFLICT


def test_unsupported_protocol_version_maps_to_its_own_code():
    assert error_code_for(UnsupportedProtocolVersionError("bad version")) == ErrorCode.UNSUPPORTED_PROTOCOL_VERSION


def test_unmapped_exception_defaults_to_internal_error():
    class SomeUnmappedError(NetraError):
        pass

    assert error_code_for(SomeUnmappedError("boom")) == ErrorCode.INTERNAL_ERROR


def test_mapping_never_inspects_the_exception_message():
    """A message engineered to look like a different failure must not
    change the mapped code: only the exception's type may select a code."""

    exc = AuthorizationError("SESSION_VERSION_CONFLICT pretend-injected-code")
    assert error_code_for(exc) == ErrorCode.AUTHORIZATION_DENIED


def test_version_conflict_and_request_id_conflict_are_not_retryable():
    assert is_retryable(ErrorCode.SESSION_VERSION_CONFLICT) is False
    assert is_retryable(ErrorCode.REQUEST_ID_CONFLICT) is False


def test_resource_and_provider_unavailable_are_retryable():
    assert is_retryable(ErrorCode.RESOURCE_UNAVAILABLE) is True
    assert is_retryable(ErrorCode.PROVIDER_UNAVAILABLE) is True
