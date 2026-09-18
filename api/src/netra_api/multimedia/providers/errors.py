"""Netra-owned failure types for multimedia provider adapters.

An adapter's job is not only to convert a provider's successes into
Netra types — it is to convert its failures too. A caller that has to
catch a Twelve Labs SDK exception has a provider dependency in its
business logic, which is what the runtime baseline's "Keep every
provider SDK behind an adapter" forbids.

The distinctions here are the ones that change what a caller does:

- transient (retry with backoff) versus permanent (do not retry),
- "this media is wrong" versus "this provider is down",
- "the provider answered something unusable" versus "it did not answer".

backend-data.md requires exactly this split: "Do not retry denied
access, invalid input or exhausted quota indiscriminately."
"""

from __future__ import annotations

from typing import Optional

from netra_api.platform.errors import NetraError


class ProviderError(NetraError):
    """Base class for every multimedia provider failure."""

    retryable: bool = False

    def __init__(self, provider: str, message: str) -> None:
        self.provider = provider
        super().__init__(f"{provider}: {message}")


class ProviderUnavailableError(ProviderError):
    """The provider could not be reached or returned a server-side failure.

    Retryable: nothing is known to be wrong with the request or the
    media.
    """

    retryable = True


class ProviderTimeoutError(ProviderError):
    """The call exceeded its deadline.

    Retryable, but with a caveat the caller must respect: a timeout is
    not proof the provider did nothing. An indexing request that timed
    out may still be running, so a retry must reconcile against a
    recorded remote operation id rather than blindly starting again
    (backend-data.md: "Do not blindly repeat an external call after an
    uncertain completion").
    """

    retryable = True

    def __init__(self, provider: str, message: str, *, operation_id: Optional[str] = None) -> None:
        self.operation_id = operation_id
        super().__init__(provider, message)


class ProviderQuotaExceededError(ProviderError):
    """The account's quota or rate budget is exhausted.

    Not retryable on this attempt's schedule: retrying immediately spends
    the same exhausted budget and delays recovery for every other job.
    """

    retryable = False


class ProviderAccessDeniedError(ProviderError):
    """The provider rejected the credentials or the account's permissions.

    Never retryable. Configuration is wrong, or the account does not have
    the capability; both need a person.
    """

    retryable = False


class ProviderRejectedMediaError(ProviderError):
    """The provider refused to ingest this specific media.

    Never retryable: the same file will be refused again. Maps to
    netra_api.multimedia.video.readiness.AnalysisUnreadyReason.PROVIDER_REJECTED_MEDIA,
    which is what the student is eventually told.
    """

    retryable = False


class MalformedProviderResponseError(ProviderError):
    """The provider answered, but its response cannot be trusted as evidence.

    Raised by the adapter's own validation: a time range running
    backwards, a range past the end of the media, an empty description
    where text was required. multimedia.md: "Do not publish an incomplete
    or failed asset as validated evidence" — and a response that is
    internally inconsistent about *when* something happened cannot be
    cited at all, because the citation is the timestamp.

    Not retryable by default. A provider that returned nonsense once will
    usually return it again for the same input, and retrying would spend
    the turn's budget discovering that.
    """

    retryable = False

    def __init__(self, provider: str, message: str, *, field: Optional[str] = None) -> None:
        self.field = field
        super().__init__(provider, message)


class ProviderCancelledError(ProviderError):
    """The work was cancelled before the provider call completed.

    Distinct from a timeout: cancellation is Netra's own decision, made
    because the student pressed STOP, the turn was superseded or the job
    lost its lease. A cancelled call must not be retried within the same
    unit of work, and anything it produced must be dropped rather than
    delivered (current-scope.md: "Cancelled or disconnected generations
    cannot resume audio").
    """

    retryable = False


class ProviderNotReadyError(ProviderError):
    """The provider accepted the work but has not finished it yet.

    Retryable on the job's backoff schedule: an index that is still
    building will usually be ready later, and nothing about the request
    is wrong. Distinct from ProviderUnavailableError so a trace shows
    "still processing" rather than "provider down".
    """

    retryable = True


class ProviderConfigurationError(ProviderError):
    """Netra's own configuration for this adapter is missing or invalid.

    Never retryable and never a provider fault. Raised before any call is
    made, so a missing index id or model pin cannot fall back to a
    provider default (runtime-baseline.md: "Keep model IDs, endpoint
    versions ... as separate explicit configuration").
    """

    retryable = False


class MediaNotIngestibleError(ProviderError):
    """Netra has no permitted, fetchable copy of this media for the provider.

    Not the provider refusing the media (that is
    ProviderRejectedMediaError): nothing was sent. A YouTube selection
    with no approved analysis path, or an upload whose object cannot be
    presented to the provider, ends here. Maps to "analysis unready",
    never to "playback unavailable" — the two are separate verdicts.
    """

    retryable = False


class ExtractionUnsupportedError(ProviderError):
    """No approved extractor exists for this kind of object.

    Returned instead of a guessed structure. A figure or diagram whose
    axes/connectivity cannot be read by an approved extractor is an
    explicit unsupported capability, not an empty structure that looks
    like "nothing there".
    """

    retryable = False


_STATUS_TO_ERROR: dict[int, type[ProviderError]] = {
    400: ProviderRejectedMediaError,
    401: ProviderAccessDeniedError,
    403: ProviderAccessDeniedError,
    404: ProviderRejectedMediaError,
    408: ProviderTimeoutError,
    409: ProviderNotReadyError,
    413: ProviderRejectedMediaError,
    415: ProviderRejectedMediaError,
    422: ProviderRejectedMediaError,
    429: ProviderQuotaExceededError,
}

_TIMEOUT_TYPE_NAMES = frozenset(
    {"TimeoutError", "ReadTimeout", "ConnectTimeout", "WriteTimeout", "PoolTimeout", "TimeoutException"}
)
_CONNECTION_TYPE_NAMES = frozenset(
    {"ConnectError", "ConnectionError", "RemoteProtocolError", "NetworkError", "TransportError"}
)
_NAME_TO_ERROR: dict[str, type[ProviderError]] = {
    # tavily-python 0.7.x names its failures rather than exposing a status.
    "InvalidAPIKeyError": ProviderAccessDeniedError,
    "MissingAPIKeyError": ProviderAccessDeniedError,
    "ForbiddenError": ProviderAccessDeniedError,
    "UsageLimitExceededError": ProviderQuotaExceededError,
    "TavilyKeylessLimitError": ProviderQuotaExceededError,
    "BadRequestError": ProviderRejectedMediaError,
}


def _status_code(exc: BaseException) -> Optional[int]:
    for attribute in ("status_code", "status"):
        value = getattr(exc, attribute, None)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def convert_provider_exception(
    provider: str,
    exc: BaseException,
    *,
    operation: str,
    operation_id: Optional[str] = None,
) -> ProviderError:
    """Translate any SDK/transport failure into a Netra-owned ProviderError.

    Classification uses only the exception's type name and HTTP status,
    never its message: provider messages may echo request content,
    signed URLs or account details, and none of that may reach a log,
    a trace or the student. The resulting message names the operation
    and the classification only.

    asyncio.CancelledError is deliberately not accepted here. Converting
    it would swallow task cancellation; callers re-raise it unchanged.
    """

    if isinstance(exc, ProviderError):
        return exc

    type_name = type(exc).__name__
    status = _status_code(exc)

    if type_name in _TIMEOUT_TYPE_NAMES or isinstance(exc, TimeoutError):
        return ProviderTimeoutError(provider, f"{operation} timed out", operation_id=operation_id)
    if type_name in _NAME_TO_ERROR:
        error_type = _NAME_TO_ERROR[type_name]
        return error_type(provider, f"{operation} failed ({error_type.__name__})")
    if status is not None:
        if status >= 500:
            return ProviderUnavailableError(provider, f"{operation} failed with status {status}")
        error_type = _STATUS_TO_ERROR.get(status)
        if error_type is ProviderTimeoutError:
            return ProviderTimeoutError(provider, f"{operation} timed out (status {status})", operation_id=operation_id)
        if error_type is not None:
            return error_type(provider, f"{operation} failed with status {status}")
        return MalformedProviderResponseError(provider, f"{operation} failed with unexpected status {status}")
    if type_name in _CONNECTION_TYPE_NAMES or isinstance(exc, ConnectionError):
        return ProviderUnavailableError(provider, f"{operation} could not reach the provider")
    return ProviderUnavailableError(provider, f"{operation} failed ({type_name})")


def error_code(error: ProviderError) -> str:
    """Closed, trace-safe code for one provider failure class."""

    return {
        ProviderUnavailableError: "provider_unavailable",
        ProviderTimeoutError: "provider_timeout",
        ProviderQuotaExceededError: "provider_quota_exceeded",
        ProviderAccessDeniedError: "provider_access_denied",
        ProviderRejectedMediaError: "provider_rejected_media",
        MalformedProviderResponseError: "provider_malformed_response",
        ProviderCancelledError: "provider_cancelled",
        ProviderNotReadyError: "provider_not_ready",
        ProviderConfigurationError: "provider_configuration_error",
        MediaNotIngestibleError: "media_not_ingestible",
        ExtractionUnsupportedError: "extraction_unsupported",
    }.get(type(error), "provider_error")
