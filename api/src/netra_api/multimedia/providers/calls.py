"""One bounded provider attempt: cancellation, timeout, conversion, span.

Every multimedia adapter makes its external calls through
``call_provider`` so the same rules hold everywhere:

- Cancellation is checked before the call starts. A cancelled unit of
  work does not spend provider budget (ProviderCancelledError).
- The call is bounded by an explicit timeout. A timeout becomes
  ProviderTimeoutError carrying any known remote operation id, because a
  timeout is not proof the provider did nothing.
- asyncio.CancelledError propagates unchanged. Converting it would
  swallow task cancellation (STOP, superseded turn, lost lease).
- Every other failure is converted to a Netra-owned ProviderError by
  type/status only; provider messages never reach a log or a trace.
- One attempt, one span. Retry policy belongs to the caller: the worker's
  job backoff for durable jobs, M1's shared 4/6/20 turn budget for
  query-time calls. An adapter that retried internally would spend that
  budget invisibly.

Synchronous SDK methods run in a worker thread so the event loop keeps
serving other requests; asynchronous ones are awaited directly.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, Awaitable, Callable, Optional, Protocol, TypeVar

from netra_api.multimedia.providers.errors import (
    ProviderCancelledError,
    ProviderError,
    ProviderTimeoutError,
    convert_provider_exception,
    error_code,
)
from netra_api.multimedia.tracing import media_span
from netra_api.platform.tracing import Tracer

T = TypeVar("T")


class CancellationSignal(Protocol):
    """Anything that can say whether the owning work should stop.

    Structurally compatible with the worker's CancellationToken, so a
    job's lease-aware token can be passed straight through.
    """

    def is_cancelled(self) -> bool:
        ...


async def _invoke(function: Callable[[], Any]) -> Any:
    """Await async callables on the loop; run blocking ones in a thread.

    Pass async provider methods directly or via functools.partial so they
    are recognised here. A lambda wrapping an async method is still
    handled, but its coroutine is created in the worker thread; if the
    call is cancelled before it is awaited, it is closed rather than
    leaked.
    """

    if inspect.iscoroutinefunction(function):
        return await function()
    result = await asyncio.to_thread(function)
    if inspect.isawaitable(result):
        try:
            return await result
        except asyncio.CancelledError:
            close = getattr(result, "close", None)
            if close is not None:
                close()
            raise
    return result


async def call_provider(
    function: Callable[[], Any] | Callable[[], Awaitable[Any]],
    *,
    provider: str,
    operation: str,
    timeout_seconds: float,
    cancellation: Optional[CancellationSignal] = None,
    tracer: Optional[Tracer] = None,
    attempt: Optional[int] = None,
    operation_id: Optional[str] = None,
    span_fields: Optional[dict[str, Any]] = None,
    discard_on_cancel: bool = True,
) -> Any:
    """Run one provider call under Netra's rules; see the module docstring.

    discard_on_cancel=False is for calls whose result records an external
    effect that already happened (an asset or index id). Dropping that id
    because the work was cancelled afterwards would make the retry create
    a duplicate; the caller records it and stops at the next stage
    boundary instead.
    """

    fields = dict(span_fields or {})
    fields.setdefault("provider", provider)
    with media_span(tracer, f"media.provider.{operation}", operation=operation, attempt=attempt, **fields) as span:
        if cancellation is not None and cancellation.is_cancelled():
            error = ProviderCancelledError(provider, f"{operation} cancelled before the call started")
            span.fail("cancelled", error_code(error))
            raise error
        if timeout_seconds <= 0:
            error = ProviderTimeoutError(provider, f"{operation} had no time remaining", operation_id=operation_id)
            span.fail("timeout", error_code(error))
            raise error
        try:
            result = await asyncio.wait_for(_invoke(function), timeout=timeout_seconds)
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            error = ProviderTimeoutError(provider, f"{operation} timed out", operation_id=operation_id)
            span.fail("timeout", error_code(error))
            raise error from None
        except ProviderError as error:
            span.fail("error", error_code(error))
            raise
        except Exception as exc:  # SDK/transport failure: classify, never echo
            error = convert_provider_exception(provider, exc, operation=operation, operation_id=operation_id)
            span.fail("timeout" if isinstance(error, ProviderTimeoutError) else "error", error_code(error))
            raise error from None
        if discard_on_cancel and cancellation is not None and cancellation.is_cancelled():
            # The call finished, but its owner no longer wants the result.
            # Drop it rather than deliver it; the caller's stage/lease
            # logic decides what, if anything, is recoverable.
            error = ProviderCancelledError(provider, f"{operation} result discarded after cancellation")
            span.fail("cancelled", error_code(error))
            raise error
        span.set(netra_outcome="ok")
        return result
