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
