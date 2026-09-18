"""Provider-error conversion and the bounded call wrapper.

Test-only doubles; no provider is called.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from fixtures.provider_fakes import Flag, InvalidAPIKeyError, ReadTimeout, StatusError, local_tracer
from netra_api.multimedia.providers.calls import call_provider
from netra_api.multimedia.providers.errors import (
    MalformedProviderResponseError,
    ProviderAccessDeniedError,
    ProviderCancelledError,
    ProviderNotReadyError,
    ProviderQuotaExceededError,
    ProviderRejectedMediaError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    convert_provider_exception,
    error_code,
)


@pytest.mark.parametrize(
    "status, expected, retryable",
    [
        (400, ProviderRejectedMediaError, False),
        (401, ProviderAccessDeniedError, False),
        (403, ProviderAccessDeniedError, False),
        (408, ProviderTimeoutError, True),
        (409, ProviderNotReadyError, True),
        (422, ProviderRejectedMediaError, False),
        (429, ProviderQuotaExceededError, False),
        (500, ProviderUnavailableError, True),
        (503, ProviderUnavailableError, True),
        (418, MalformedProviderResponseError, False),
    ],
)
def test_status_codes_convert_to_netra_errors_with_retryability(status, expected, retryable):
    error = convert_provider_exception("twelvelabs", StatusError(status), operation="search")
    assert type(error) is expected
    assert error.retryable is retryable


def test_conversion_never_echoes_the_provider_message():
    error = convert_provider_exception("twelvelabs", StatusError(500), operation="analyze")
    assert "secret" not in str(error) and "acct_123" not in str(error)
    named = convert_provider_exception("tavily", InvalidAPIKeyError("key tvly-SECRET is invalid"), operation="search")
    assert isinstance(named, ProviderAccessDeniedError)
    assert "tvly-SECRET" not in str(named)


def test_transport_timeouts_and_connection_errors_are_classified_by_type():
    assert isinstance(convert_provider_exception("x", ReadTimeout(), operation="o", operation_id="op-1"), ProviderTimeoutError)
    assert convert_provider_exception("x", ReadTimeout(), operation="o", operation_id="op-1").operation_id == "op-1"
    assert isinstance(convert_provider_exception("x", ConnectionError("refused"), operation="o"), ProviderUnavailableError)


async def test_cancelled_work_never_starts_the_call():
    calls = []
    with pytest.raises(ProviderCancelledError):
        await call_provider(lambda: calls.append(1), provider="p", operation="op", timeout_seconds=1, cancellation=Flag(True))
    assert calls == []


async def test_timeout_carries_the_remote_operation_id():
    async def slow():
        await asyncio.sleep(1)

    with pytest.raises(ProviderTimeoutError) as caught:
        await call_provider(slow, provider="p", operation="index_asset", timeout_seconds=0.01, operation_id="asset-9")
    assert caught.value.operation_id == "asset-9"


async def test_no_remaining_time_is_a_timeout_without_a_call():
    calls = []
    with pytest.raises(ProviderTimeoutError):
        await call_provider(lambda: calls.append(1), provider="p", operation="op", timeout_seconds=0)
    assert calls == []


async def test_task_cancellation_propagates_unchanged():
    async def slow():
        await asyncio.sleep(5)

    task = asyncio.create_task(call_provider(slow, provider="p", operation="op", timeout_seconds=10))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_result_after_cancellation_is_discarded_unless_it_records_an_effect():
    flag = Flag(False)

    def effect():
        flag.cancelled = True  # cancelled while the provider call was in flight
        return "asset-1"

    with pytest.raises(ProviderCancelledError):
        await call_provider(effect, provider="p", operation="describe", timeout_seconds=1, cancellation=flag)

    flag.cancelled = False
    kept = await call_provider(effect, provider="p", operation="create_asset", timeout_seconds=1, cancellation=flag, discard_on_cancel=False)
    assert kept == "asset-1"


async def test_synchronous_sdk_calls_run_off_the_event_loop():
    def blocking():
        time.sleep(0.05)
        return "done"

    ticks = 0

    async def ticker():
        nonlocal ticks
        for _ in range(5):
            await asyncio.sleep(0.005)
            ticks += 1

    result, _ = await asyncio.gather(
        call_provider(blocking, provider="p", operation="op", timeout_seconds=1), ticker()
    )
    assert result == "done" and ticks == 5


async def test_one_attempt_one_span_with_safe_codes_only():
    tracer, exporter = local_tracer()

    def failing():
        raise StatusError(429, "quota for account acct_123 exhausted")

    with pytest.raises(ProviderQuotaExceededError):
        await call_provider(failing, provider="twelvelabs", operation="marengo_search", timeout_seconds=1, tracer=tracer, attempt=2)
    tracer.shutdown(1)

    assert len(exporter.spans) == 1
    span = exporter.spans[0]
    assert span.name == "media.provider.marengo_search"
    assert span.status == "error"
    assert span.attributes["netra.error_code"] == "provider_quota_exceeded"
    assert span.attributes["netra.attempt"] == 2
    assert span.attributes["llm.provider"] == "twelvelabs"
    assert "acct_123" not in repr(exporter.spans)
    assert tracer.diagnostics.attributes_dropped == 0


def test_every_error_class_has_a_closed_code():
    for error in (
        ProviderTimeoutError("p", "m"),
        ProviderQuotaExceededError("p", "m"),
        ProviderNotReadyError("p", "m"),
        MalformedProviderResponseError("p", "m"),
    ):
        assert error_code(error) != "provider_error"
