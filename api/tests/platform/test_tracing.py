"""Tracing boundary: allowlist, context isolation, non-blocking bounded export, loss and shutdown.

Exporters here are local test exporters (in-memory, stalled, failing). No
AX endpoint, OpenTelemetry package or network is involved; these tests prove
the Netra-owned boundary, not AX ingestion.
"""

import asyncio
import threading
import time

import pytest

from netra_api.platform.errors import ResourceUnavailableError
from netra_api.platform.tracing import (
    ExportSettings,
    InMemorySpanExporter,
    build_tracer,
    disable_langsmith_export,
    sanitize_attributes,
)

FAST = ExportSettings(max_queue_size=64, max_batch_size=16, schedule_delay_seconds=0.01, export_timeout_seconds=0.2, max_retries=2, retry_backoff_seconds=0.01)


class StalledExporter:
    def __init__(self):
        self.release = threading.Event()
        self.calls = 0

    def export(self, batch):
        self.calls += 1
        self.release.wait(5)
        return "success"

    def shutdown(self):
        self.release.set()


class ScriptedExporter:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def export(self, batch):
        self.calls += 1
        outcome = self.outcomes.pop(0) if self.outcomes else "success"
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def shutdown(self):
        pass


def _wait(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "condition not met"
        time.sleep(0.005)


def test_allowlist_drops_unknown_keys_and_free_text():
    clean, dropped = sanitize_attributes(
        {
            "netra.request_id": "5f1b2c3d-1111-4a2b-9c3d-000000000001",
            "netra.outcome": "handled",
            "netra.evidence_ids": ["ev-fig02", "ev-table-tbl01"],
            "utterance": "How does this line show constant resistance?",
            "netra.outcome_text": "x",
            "netra.error_code": "Traceback (most recent call last): secret",
            "netra.request_id_nested": {"a": 1},
            "netra.tool": "https://example.com/?token=abc",
            "netra.session_version": True,
            "netra.evidence_count": None,
        }
    )
    assert clean == {
        "netra.request_id": "5f1b2c3d-1111-4a2b-9c3d-000000000001",
        "netra.outcome": "handled",
        "netra.evidence_ids": ["ev-fig02", "ev-table-tbl01"],
    }
    assert dropped == 6


def test_error_spans_record_only_a_safe_code_never_the_message():
    exporter = InMemorySpanExporter()
    tracer = build_tracer("local", exporter=exporter, settings=FAST)
    with pytest.raises(ResourceUnavailableError):
        with tracer.span("netra.request", netra_operation="client_message"):
            raise ResourceUnavailableError("database password=hunter2 at 10.0.0.5")
    tracer.shutdown(1)
    span = exporter.spans[0]
    assert span.status == "error" and span.attributes["netra.error_code"] == "resource_unavailable"
    assert "hunter2" not in repr(exporter.spans)


def test_events_are_sanitized_like_attributes():
    exporter = InMemorySpanExporter()
    tracer = build_tracer("local", exporter=exporter, settings=FAST)
    with tracer.span("netra.request") as span:
        span.event("evidence_gap", **{"netra.requirement": "x-axis", "text": "student private answer 8 volts"})
        span.event("Free text event name", **{"netra.gap": "x"})
    tracer.shutdown(1)
    events = exporter.spans[0].events
    assert [e.name for e in events] == ["evidence_gap"]
    assert events[0].attributes == {"netra.requirement": "x-axis"}
    assert tracer.diagnostics.attributes_dropped >= 2


async def test_concurrent_requests_keep_isolated_span_context():
    exporter = InMemorySpanExporter()
    tracer = build_tracer("local", exporter=exporter, settings=FAST)

    async def request(name):
        with tracer.span("netra.request", netra_request_id=name) as root:
            await asyncio.sleep(0.01)
            with tracer.span("netra.tool", netra_tool=name):
                await asyncio.sleep(0.01)
                assert tracer.current().parent_span_id == root.span_id
            child = asyncio.ensure_future(_child(tracer, name))
            await child
            return root.trace_id

    trace_ids = await asyncio.gather(*(request(f"req-{i}") for i in range(20)))
    tracer.shutdown(2)
    assert len(set(trace_ids)) == 20
    for trace_id, spans in exporter.by_trace().items():
        names = {s.attributes.get("netra.request_id") or s.attributes.get("netra.tool") for s in spans} - {None}
        assert len(names) == 1 and len(spans) == 3  # nothing from another request leaked into this trace


async def _child(tracer, name):
    with tracer.span("netra.model.decision", netra_attempt=1):
        await asyncio.sleep(0)


async def test_cancellation_is_recorded_and_propagates_unchanged():
    exporter = InMemorySpanExporter()
    tracer = build_tracer("local", exporter=exporter, settings=FAST)

    async def work():
        with tracer.span("netra.coordinator.turn"):
            await asyncio.sleep(10)

    task = asyncio.ensure_future(work())
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    tracer.shutdown(1)
    assert exporter.spans[0].status == "cancelled"


def test_stalled_exporter_never_blocks_span_end_and_overflow_is_counted():
    exporter = StalledExporter()
    tracer = build_tracer("local", exporter=exporter, settings=FAST)
    started = time.perf_counter()
    for _ in range(500):
        with tracer.span("netra.request"):
            pass
    elapsed = time.perf_counter() - started
    assert elapsed < 0.5  # 500 spans ended while the exporter is stalled
    diagnostics = tracer.diagnostics
    assert diagnostics.dropped_queue_full > 0
    assert diagnostics.created == diagnostics.ended == 500
    shutdown_started = time.perf_counter()
    tracer.shutdown(0.3)
    assert time.perf_counter() - shutdown_started < 1.0
    assert diagnostics.final_flush == "incomplete"
    assert diagnostics.exported + diagnostics.lost == diagnostics.ended  # every span accounted for


def test_rate_limited_export_retries_then_counts_loss():
    exporter = ScriptedExporter(["retryable_failure"] * 3)
    tracer = build_tracer("local", exporter=exporter, settings=FAST)
    with tracer.span("netra.request"):
        pass
    _wait(lambda: tracer.diagnostics.dropped_after_failure == 1)
    assert exporter.calls == 3 and tracer.diagnostics.export_retries == 2
    tracer.shutdown(1)


def test_permanent_failure_and_exporter_exception_are_not_retried():
    exporter = ScriptedExporter([RuntimeError("401 invalid api key abc123"), "permanent_failure"])
    tracer = build_tracer("local", exporter=exporter, settings=FAST)
    with tracer.span("netra.request"):
        pass
    _wait(lambda: tracer.diagnostics.dropped_after_failure == 1)
    assert exporter.calls == 1
    assert tracer.diagnostics.last_error_code == "exporter_runtimeerror"
    assert "abc123" not in str(tracer.diagnostics.snapshot())
    tracer.shutdown(1)


def test_export_timeout_is_bounded_and_counted():
    exporter = StalledExporter()
    tracer = build_tracer("local", exporter=exporter, settings=FAST)
    with tracer.span("netra.request"):
        pass
    _wait(lambda: tracer.diagnostics.export_timeouts >= 1, timeout=3)
    tracer.shutdown(0.2)


def test_orderly_shutdown_flushes_everything_with_a_healthy_exporter():
    exporter = InMemorySpanExporter()
    tracer = build_tracer("local", exporter=exporter, settings=ExportSettings(schedule_delay_seconds=60))
    for _ in range(40):
        with tracer.span("netra.request"):
            pass
    tracer.shutdown(2)
    assert len(exporter.spans) == 40
    assert tracer.diagnostics.final_flush == "complete" and tracer.diagnostics.lost == 0


def test_spans_after_shutdown_are_counted_not_exported():
    exporter = InMemorySpanExporter()
    tracer = build_tracer("local", exporter=exporter, settings=FAST)
    tracer.shutdown(1)
    with tracer.span("netra.request"):
        pass
    assert tracer.diagnostics.dropped_at_shutdown == 1 and exporter.spans == []


def test_ax_mode_without_credentials_is_a_visible_config_error_not_a_boot_failure(monkeypatch):
    monkeypatch.delenv("ARIZE_SPACE_ID", raising=False)
    monkeypatch.delenv("ARIZE_API_KEY", raising=False)
    tracer = build_tracer("ax")
    assert tracer.enabled is False
    assert tracer.diagnostics.configuration_errors == 1
    assert tracer.diagnostics.last_error_code == "ax_not_configured"
    with tracer.span("netra.request") as span:
        span.set(netra_outcome="handled")


def test_off_mode_creates_nothing():
    tracer = build_tracer("off")
    with tracer.span("netra.request"):
        pass
    assert tracer.diagnostics.created == 0


def test_langsmith_export_is_forced_off_and_reported():
    environ = {"LANGSMITH_TRACING": "true", "LANGCHAIN_TRACING_V2": "false"}
    assert disable_langsmith_export(environ) == ["LANGSMITH_TRACING"]
    assert all(environ[name] == "false" for name in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGCHAIN_TRACING"))


class RecordingExporter:
    """Records every span id it receives (repeats included); optionally slow or gated."""

    def __init__(self, delay=0.0, gate=None):
        self.delay = delay
        self.gate = gate
        self.calls = 0
        self.received = {}
        self._lock = threading.Lock()

    def export(self, batch):
        with self._lock:
            self.calls += 1
        if self.gate is not None:
            self.gate.wait(5)
        if self.delay:
            time.sleep(self.delay)
        with self._lock:
            for span in batch:
                self.received[span.span_id] = self.received.get(span.span_id, 0) + 1
        return "success"

    def shutdown(self):
        pass


def test_a_slow_exporter_never_receives_a_batch_twice_and_its_late_success_is_counted():
    exporter = RecordingExporter(delay=0.3)  # slower than FAST's 0.2 s per-call timeout
    tracer = build_tracer("local", exporter=exporter, settings=FAST)
    for _ in range(5):
        with tracer.span("netra.request"):
            pass
    _wait(lambda: len(exporter.received) == 5 and tracer.diagnostics.exported == 5, timeout=3)
    time.sleep(0.8)  # a resubmitted copy of a timed-out batch would have arrived by now
    diagnostics = tracer.diagnostics
    assert max(exporter.received.values()) == 1
    assert diagnostics.export_timeouts >= 1 and diagnostics.dropped_after_failure == 0
    assert diagnostics.exported == 5 and diagnostics.exported_late == 5
    tracer.shutdown(1)
    assert diagnostics.exported + diagnostics.lost == diagnostics.ended


def test_a_stalled_exporter_gets_no_queued_work_and_what_it_delivers_is_what_is_counted():
    exporter = RecordingExporter(gate=threading.Event())
    tracer = build_tracer("local", exporter=exporter, settings=FAST)
    for _ in range(48):
        with tracer.span("netra.request"):
            pass
    time.sleep(1.0)  # several per-call timeouts pass while the first call is stuck
    assert exporter.calls == 1
    exporter.gate.set()
    time.sleep(0.6)
    diagnostics = tracer.diagnostics
    assert max(exporter.received.values()) == 1  # nothing delivered twice
    assert diagnostics.exported == len(exporter.received)  # delivered == counted as exported
    tracer.shutdown(1)
    assert diagnostics.exported + diagnostics.lost == diagnostics.ended


def test_a_delivery_after_the_final_flush_is_reported_rather_than_silently_lost():
    exporter = RecordingExporter(gate=threading.Event())
    tracer = build_tracer("local", exporter=exporter, settings=FAST)
    with tracer.span("netra.request"):
        pass
    _wait(lambda: exporter.calls == 1)
    tracer.shutdown(0.3)
    diagnostics = tracer.diagnostics
    assert diagnostics.final_flush == "incomplete"
    assert diagnostics.exported == 0 and diagnostics.exported + diagnostics.lost == diagnostics.ended
    exporter.gate.set()
    _wait(lambda: diagnostics.delivered_after_final_flush == 1)
    assert len(exporter.received) == 1
    assert diagnostics.exported + diagnostics.lost == diagnostics.ended  # totals frozen at shutdown
