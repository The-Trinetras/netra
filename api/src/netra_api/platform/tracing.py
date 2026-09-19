"""Netra's tracing boundary: sanitized spans, isolated context, bounded background export.

Implements the M1 part of docs/architecture/arize-ax-integration.md. Domain
code depends only on ``Tracer`` / ``SpanHandle`` from this module; no AX,
OpenTelemetry or OpenInference type crosses into services.

Guarantees, each covered by tests:

1. **Allowlist before export.** Every attribute and event attribute passes
   ``sanitize_attributes``: unknown keys are dropped (and counted), values must
   match the key's declared kind (opaque identifier, closed code, number,
   boolean, bounded identifier list). Free text — utterances, evidence text,
   prompts, answers, exception messages, URLs — has no permitted key, so it
   cannot be exported even if a caller passes it. Key-name redaction alone is
   not relied on.
2. **Async context isolation.** The current span lives in a ContextVar, so
   concurrent requests and asyncio tasks never share a parent span.
3. **Nothing on the response path waits for export.** Ending a span only
   appends an immutable record to a bounded in-memory queue (``put_nowait``).
   A single background thread batches and exports with a per-call timeout
   and bounded retries. A full queue drops the span and counts it.
4. **At most one export call in flight; no duplicate delivery.** A call that
   exceeds its timeout may still deliver, so it is never resubmitted; its
   eventual outcome settles the batch once. While it is still running no
   other call is made: spans wait in the bounded queue, where a full queue
   drops and counts new spans.
5. **Visible, exact loss.** ``TracingDiagnostics`` counts created/ended/
   enqueued/exported/failed/dropped spans, attributes dropped, retries,
   configuration errors and the final flush outcome, and every enqueued span
   is settled exactly once (``exported + lost == ended``). Diagnostics
   logging is rate-limited.
6. **Bounded shutdown.** ``shutdown(timeout)`` drains until a deadline and
   counts everything left as ``dropped_at_shutdown``; it never blocks
   indefinitely and is never called per turn. Totals are frozen at the final
   flush: a stuck call that delivers afterwards is reported separately as
   ``delivered_after_final_flush``.

Identity: ``trace_id``/``span_id`` are diagnostic identities generated here.
The logical ``request_id`` is recorded as an attribute; a retransmission is a
new span with the same request_id, never a fabricated second effect.

The AX/OTLP exporter is NOT implemented: OpenTelemetry/OpenInference pins are
unreviewed and the packages are not installed (dependency request recorded for
M2). Until then ``mode="ax"`` reports a configuration error and exports nothing.
"""

from __future__ import annotations

import contextvars
import logging
import queue
import re
import secrets
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeout, wait as wait_futures
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterator, Literal, Optional, Protocol
from contextlib import contextmanager

logger = logging.getLogger("netra.tracing")

SpanStatus = Literal["ok", "error", "cancelled", "timeout"]
ExportOutcome = Literal["success", "retryable_failure", "permanent_failure"]

# ---------------------------------------------------------------------------
# Allowlist
# ---------------------------------------------------------------------------

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:\-]{0,127}$")
_CODE = re.compile(r"^[a-z0-9][a-z0-9_.:\-]{0,63}$")
_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/\-]{0,127}$")
"""Model ids, which may carry a provider prefix: "google/gemini-3.8-flash"."""
_MAX_LIST = 16


def _is_id(value: Any) -> bool:
    return isinstance(value, str) and bool(_ID.match(value))


def _is_model(value: Any) -> bool:
    return isinstance(value, str) and bool(_MODEL.match(value))


def _is_code(value: Any) -> bool:
    return isinstance(value, str) and bool(_CODE.match(value))


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and -(2**53) < value < 2**53


def _is_number(value: Any) -> bool:
    return (_is_int(value) or isinstance(value, float)) and not isinstance(value, bool)


def _is_bool(value: Any) -> bool:
    return isinstance(value, bool)


def _id_list(value: Any) -> bool:
    return isinstance(value, (list, tuple)) and len(value) <= _MAX_LIST and all(_is_id(item) for item in value)


def _code_list(value: Any) -> bool:
    return isinstance(value, (list, tuple)) and len(value) <= _MAX_LIST and all(_is_code(item) for item in value)


Validator = Callable[[Any], bool]

ALLOWED_ATTRIBUTES: dict[str, Validator] = {
    # service / build
    "service.name": _is_code,
    "service.version": _is_id,
    # correlation (opaque identifiers only; never account ids)
    "netra.request_id": _is_id,
    "netra.message_type": _is_code,
    "netra.session_ref": _is_id,
    "netra.generation_id": _is_id,
    "netra.handoff_id": _is_id,
    "netra.eval.case_id": _is_id,
    "netra.eval.run_id": _is_id,
    # operation facts
    "netra.operation": _is_code,
    "netra.outcome": _is_code,
    "netra.error_code": _is_code,
    "netra.replayed": _is_bool,
    "netra.command": _is_code,
    "netra.navigation_unit": _is_code,
    "netra.interaction_mode": _is_code,
    "netra.session_version": _is_int,
    "netra.source_version_id": _is_id,
    "netra.changed": _is_bool,
    # budget
    "netra.budget.model_decisions_used": _is_int,
    "netra.budget.tool_calls_used": _is_int,
    "netra.budget.nested_model_calls": _is_int,
    "netra.budget.remaining_ms": _is_int,
    # provider attempt (usage absent means unknown, never zero)
    "llm.provider": _is_code,
    "llm.model_name": _is_model,
    "netra.attempt": _is_int,
    "llm.token_count.prompt": _is_int,
    "llm.token_count.completion": _is_int,
    # tools / evidence facts
    "netra.tool": _is_code,
    "netra.tool.status": _is_code,
    "netra.tool.reason": _is_code,
    "netra.evidence_ids": _id_list,
    "netra.evidence_count": _is_int,
    "netra.rejected_count": _is_int,
    "netra.requirement": _is_code,
    "netra.requirements": _code_list,
    "netra.gap": _is_code,
    "netra.gap_status": _is_code,
    "netra.tools": _code_list,
    "netra.check": _is_code,
    "netra.handoff_mode": _is_code,
    "netra.tutor_status": _is_code,
    # speech / playback: sent is distinct from played/acknowledged
    "netra.audio.frames_sent": _is_int,
    "netra.audio.cached": _is_bool,
    "netra.playback.status": _is_code,
    "netra.duration_ms": _is_number,
}
"""Every exportable attribute. Anything else is dropped before any exporter."""


def sanitize_attributes(attributes: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Return (allowed attributes, number dropped). Never raises."""

    allowed: dict[str, Any] = {}
    dropped = 0
    for key, value in attributes.items():
        validator = ALLOWED_ATTRIBUTES.get(key)
        if validator is None:
            dropped += 1
            continue
        if value is None:
            continue
        try:
            ok = validator(value)
        except Exception:
            ok = False
        if not ok:
            dropped += 1
            continue
        allowed[key] = list(value) if isinstance(value, tuple) else value
    return allowed, dropped


# ---------------------------------------------------------------------------
# Span records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SpanEvent:
    name: str
    at_unix_ns: int
    attributes: dict[str, Any]


@dataclass(frozen=True)
class SpanRecord:
    """Immutable, already-sanitized span handed to exporters."""

    trace_id: str
    span_id: str
    parent_span_id: Optional[str]
    name: str
    start_unix_ns: int
    end_unix_ns: int
    status: SpanStatus
    attributes: dict[str, Any]
    events: tuple[SpanEvent, ...]

    @property
    def duration_ms(self) -> float:
        return (self.end_unix_ns - self.start_unix_ns) / 1e6


@dataclass
class TracingDiagnostics:
    created: int = 0
    ended: int = 0
    enqueued: int = 0
    exported: int = 0
    export_failures: int = 0
    dropped_queue_full: int = 0
    dropped_after_failure: int = 0
    dropped_at_shutdown: int = 0
    attributes_dropped: int = 0
    export_attempts: int = 0
    export_retries: int = 0
    export_timeouts: int = 0
    configuration_errors: int = 0
    exported_late: int = 0
    """Spans whose export call exceeded its timeout but then succeeded (included in ``exported``)."""
    export_skipped_stalled: int = 0
    """Batches not sent because an earlier call was still in flight (their spans count as lost)."""
    delivered_after_final_flush: int = 0
    """Spans a stuck call delivered after shutdown had already counted them lost."""
    last_error_code: Optional[str] = None
    final_flush: Optional[str] = None
    queue_high_water: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, **increments: int) -> None:
        with self._lock:
            for key, amount in increments.items():
                setattr(self, key, getattr(self, key) + amount)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {k: v for k, v in self.__dict__.items() if not k.startswith("_")}

    @property
    def lost(self) -> int:
        return self.dropped_queue_full + self.dropped_after_failure + self.dropped_at_shutdown


# ---------------------------------------------------------------------------
# Exporters
# ---------------------------------------------------------------------------


class SpanExporter(Protocol):
    """Called ONLY from the background export thread, never from request code."""

    def export(self, batch: list[SpanRecord]) -> ExportOutcome:
        ...

    def shutdown(self) -> None:
        ...


class InMemorySpanExporter:
    """Keeps exported spans in memory: tests, fixture evaluation and trace manifests.

    Bounded: beyond ``max_spans`` it refuses batches (permanent failure) so
    the processor counts them as lost instead of growing without limit.
    """

    def __init__(self, max_spans: int = 50_000) -> None:
        self.spans: list[SpanRecord] = []
        self.max_spans = max_spans
        self._lock = threading.Lock()

    def export(self, batch: list[SpanRecord]) -> ExportOutcome:
        with self._lock:
            if len(self.spans) + len(batch) > self.max_spans:
                return "permanent_failure"
            self.spans.extend(batch)
        return "success"

    def shutdown(self) -> None:
        pass

    def by_trace(self) -> dict[str, list[SpanRecord]]:
        grouped: dict[str, list[SpanRecord]] = {}
        with self._lock:
            for span in self.spans:
                grouped.setdefault(span.trace_id, []).append(span)
        return grouped


# ---------------------------------------------------------------------------
# Background batch processor
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExportSettings:
    max_queue_size: int = 2048
    max_batch_size: int = 128
    schedule_delay_seconds: float = 1.0
    export_timeout_seconds: float = 5.0
    max_retries: int = 2
    retry_backoff_seconds: float = 0.5
    diagnostics_log_interval_seconds: float = 60.0
    """Implementation bounds for a small evaluation workload, not product policy."""


class BatchSpanProcessor:
    def __init__(self, exporter: SpanExporter, diagnostics: TracingDiagnostics, settings: ExportSettings = ExportSettings()) -> None:
        self._exporter = exporter
        self._diagnostics = diagnostics
        self._settings = settings
        self._queue: "queue.Queue[SpanRecord]" = queue.Queue(maxsize=settings.max_queue_size)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._call_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="netra-trace-export-call")
        self._inflight: Optional[Future] = None
        self._settle_lock = threading.Lock()
        self._finalized = False
        self._worker = threading.Thread(target=self._run, name="netra-trace-export", daemon=True)
        self._last_log = 0.0
        self._worker.start()

    def on_end(self, record: SpanRecord) -> None:
        """Response-path entry point: O(1), non-blocking, never raises."""

        if self._stop.is_set():
            self._diagnostics.add(dropped_at_shutdown=1)
            return
        try:
            self._queue.put_nowait(record)
        except queue.Full:
            self._diagnostics.add(dropped_queue_full=1)
            self._log_rate_limited("span queue full; dropping spans")
            return
        self._diagnostics.add(enqueued=1)
        size = self._queue.qsize()
        if size > self._diagnostics.queue_high_water:
            self._diagnostics.queue_high_water = size
        if size >= self._settings.max_batch_size:
            self._wake.set()

    def _drain(self, limit: int) -> list[SpanRecord]:
        batch: list[SpanRecord] = []
        while len(batch) < limit:
            try:
                batch.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return batch

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(self._settings.schedule_delay_seconds)
            self._wake.clear()
            while not self._stop.is_set():
                if not self._wait_for_inflight(self._settings.schedule_delay_seconds):
                    # The collector still holds an earlier call: leave spans in
                    # the bounded queue (a full queue drops and counts new ones).
                    break
                batch = self._drain(self._settings.max_batch_size)
                if not batch:
                    break
                self._export_with_retries(batch, deadline=None)

    def _export_with_retries(self, batch: list[SpanRecord], deadline: Optional[float]) -> Optional[bool]:
        """Export one batch with at most one call in flight.

        Returns True when exported, False when counted lost, and None when a
        call timed out and is still running: that call may still deliver, so it
        is never resubmitted, and ``_settle_late`` records its outcome.
        """

        count = len(batch)
        for attempt in range(self._settings.max_retries + 1):
            if deadline is not None and time.monotonic() >= deadline:
                break
            timeout = self._settings.export_timeout_seconds
            if deadline is not None:
                timeout = max(0.0, min(timeout, deadline - time.monotonic()))
            if not self._wait_for_inflight(timeout):
                # An earlier call is still holding the collector: another call
                # would only queue behind it.
                self._diagnostics.add(export_skipped_stalled=1)
                self._diagnostics.last_error_code = "export_stalled"
                break
            self._diagnostics.add(export_attempts=1, export_retries=1 if attempt else 0)
            try:
                future = self._call_pool.submit(self._exporter.export, batch)
                outcome = future.result(timeout=timeout)
            except FutureTimeout:
                self._diagnostics.add(export_timeouts=1)
                self._diagnostics.last_error_code = "export_timeout"
                self._inflight = future
                future.add_done_callback(lambda done, count=count: self._settle_late(done, count))
                # The request path never sees any of this.
                return None
            except Exception as exc:  # exporter bug or pool shut down: record kind, never message
                self._diagnostics.last_error_code = f"exporter_{type(exc).__name__.lower()}"[:60]
                outcome = "permanent_failure"
            if outcome == "success":
                self._settle(True, count)
                return True
            self._diagnostics.add(export_failures=1)
            if outcome == "permanent_failure":
                break
            if attempt < self._settings.max_retries:
                self._stop.wait(self._settings.retry_backoff_seconds * (2**attempt))
        self._settle(False, count)
        self._log_rate_limited("span export failed; spans dropped")
        return False

    def _wait_for_inflight(self, timeout: float) -> bool:
        inflight = self._inflight
        if inflight is None:
            return True
        wait_futures([inflight], timeout=timeout)
        if not inflight.done():
            return False
        self._inflight = None
        return True

    def _settle_late(self, future: Future, count: int) -> None:
        try:
            delivered = future.result() == "success"
        except Exception:  # the exporter raised, or the call was cancelled at shutdown
            delivered = False
        if not delivered:
            self._diagnostics.add(export_failures=1)
        self._settle(delivered, count, late=True)

    def _settle(self, delivered: bool, count: int, *, late: bool = False) -> None:
        """Record a batch's outcome exactly once. After the final flush the totals
        are frozen; a late delivery is then reported, not re-counted."""

        with self._settle_lock:
            if self._finalized:
                if delivered:
                    self._diagnostics.add(delivered_after_final_flush=count)
                return
            if delivered:
                self._diagnostics.add(exported=count, exported_late=count if late else 0)
            else:
                self._diagnostics.add(dropped_after_failure=count)

    def shutdown(self, timeout_seconds: float) -> None:
        """Bounded final flush. Everything not exported by the deadline is counted lost."""

        deadline = time.monotonic() + max(0.0, timeout_seconds)
        self._stop.set()
        self._wake.set()
        self._worker.join(timeout=max(0.0, deadline - time.monotonic()))
        flushed_all = True
        if self._worker.is_alive():
            # The export thread is still inside a call; anything sent now would
            # only queue behind it.
            flushed_all = False
        else:
            while time.monotonic() < deadline:
                if not self._wait_for_inflight(max(0.0, deadline - time.monotonic())):
                    break  # the collector still holds an earlier call
                batch = self._drain(self._settings.max_batch_size)
                if not batch:
                    break
                if self._export_with_retries(batch, deadline=deadline) is False:
                    flushed_all = False
        leftover = self._drain(self._settings.max_queue_size)
        if leftover:
            self._diagnostics.add(dropped_at_shutdown=len(leftover))
            flushed_all = False
        with self._settle_lock:
            # A batch still inside an export call is neither exported nor queued:
            # count it lost now, and freeze the totals so a late outcome cannot
            # count it twice.
            d = self._diagnostics
            unaccounted = d.enqueued - (d.exported + d.dropped_after_failure + d.dropped_at_shutdown)
            if unaccounted > 0:
                d.add(dropped_at_shutdown=unaccounted)
                flushed_all = False
            self._finalized = True
        self._diagnostics.final_flush = "complete" if flushed_all else "incomplete"
        self._call_pool.shutdown(wait=False, cancel_futures=True)
        try:
            self._exporter.shutdown()
        except Exception:
            self._diagnostics.last_error_code = "exporter_shutdown_error"

    def _log_rate_limited(self, message: str) -> None:
        now = time.monotonic()
        if now - self._last_log >= self._settings.diagnostics_log_interval_seconds:
            self._last_log = now
            logger.warning("%s (diagnostics: %s)", message, self._diagnostics.snapshot())


# ---------------------------------------------------------------------------
# Tracer and spans
# ---------------------------------------------------------------------------

_CURRENT: contextvars.ContextVar[Optional["SpanHandle"]] = contextvars.ContextVar("netra_current_span", default=None)


def _new_id(nbytes: int) -> str:
    return secrets.token_hex(nbytes)


class SpanHandle:
    """A live span. Only sanitized attributes are ever stored."""

    __slots__ = ("_tracer", "trace_id", "span_id", "parent_span_id", "name", "_start", "_attributes", "_events", "_status", "_ended")

    def __init__(self, tracer: "Tracer", name: str, parent: Optional["SpanHandle"]) -> None:
        self._tracer = tracer
        self.trace_id = parent.trace_id if parent else _new_id(16)
        self.span_id = _new_id(8)
        self.parent_span_id = parent.span_id if parent else None
        self.name = name
        self._start = time.time_ns()
        self._attributes: dict[str, Any] = {}
        self._events: list[SpanEvent] = []
        self._status: SpanStatus = "ok"
        self._ended = False

    def set(self, **attributes: Any) -> None:
        clean, dropped = sanitize_attributes(_dotted(attributes))
        self._attributes.update(clean)
        if dropped:
            self._tracer.diagnostics.add(attributes_dropped=dropped)

    def event(self, name: str, **attributes: Any) -> None:
        if not _is_code(name) or len(self._events) >= 64:
            self._tracer.diagnostics.add(attributes_dropped=1)
            return
        clean, dropped = sanitize_attributes(_dotted(attributes))
        if dropped:
            self._tracer.diagnostics.add(attributes_dropped=dropped)
        self._events.append(SpanEvent(name=name, at_unix_ns=time.time_ns(), attributes=clean))

    def fail(self, status: SpanStatus, error_code: Optional[str] = None) -> None:
        self._status = status
        if error_code:
            self.set(netra_error_code=error_code)

    def end(self) -> None:
        if self._ended:
            return
        self._ended = True
        self._tracer._finish(
            SpanRecord(
                trace_id=self.trace_id,
                span_id=self.span_id,
                parent_span_id=self.parent_span_id,
                name=self.name,
                start_unix_ns=self._start,
                end_unix_ns=time.time_ns(),
                status=self._status,
                attributes=dict(self._attributes),
                events=tuple(self._events),
            )
        )


def _dotted(attributes: dict[str, Any]) -> dict[str, Any]:
    """Allow ``netra_request_id=...`` keyword spelling for ``netra.request_id``."""

    out = {}
    for key, value in attributes.items():
        if "." not in key:
            for prefix in ("netra_budget_", "netra_audio_", "netra_playback_", "netra_eval_", "netra_tool_", "llm_token_count_", "netra_", "llm_", "service_"):
                if key.startswith(prefix):
                    head = prefix.rstrip("_").replace("_", ".")
                    key = head + "." + key[len(prefix):]
                    break
        out[key] = value
    return out


class _NoopSpan:
    trace_id = span_id = parent_span_id = None
    name = "noop"

    def set(self, **attributes: Any) -> None:
        pass

    def event(self, name: str, **attributes: Any) -> None:
        pass

    def fail(self, status: SpanStatus, error_code: Optional[str] = None) -> None:
        pass

    def end(self) -> None:
        pass


NOOP_SPAN = _NoopSpan()


class Tracer:
    """One per process, created in composition. Disabled tracers create nothing."""

    def __init__(self, processor: Optional[BatchSpanProcessor], diagnostics: TracingDiagnostics, *, service_name: str = "netra-api") -> None:
        self._processor = processor
        self.diagnostics = diagnostics
        self._service_name = service_name

    @property
    def enabled(self) -> bool:
        return self._processor is not None

    @contextmanager
    def span(self, name: str, **attributes: Any) -> Iterator[Any]:
        """Start a child of the context's current span; end it on every exit path.

        Cancellation and timeouts are recorded as such; the original
        exception always propagates unchanged — tracing never alters
        control flow.
        """

        if self._processor is None:
            yield NOOP_SPAN
            return
        handle = SpanHandle(self, name, _CURRENT.get())
        self.diagnostics.add(created=1)
        handle.set(service_name=self._service_name, **attributes)
        token = _CURRENT.set(handle)
        try:
            yield handle
        except BaseException as exc:
            import asyncio

            if isinstance(exc, asyncio.CancelledError):
                handle.fail("cancelled")
            elif isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
                handle.fail("timeout", "timeout")
            elif handle._status == "ok":
                handle.fail("error", _error_code(exc))
            raise
        finally:
            _CURRENT.reset(token)
            handle.end()

    def current(self) -> Any:
        return _CURRENT.get() or NOOP_SPAN

    def _finish(self, record: SpanRecord) -> None:
        self.diagnostics.add(ended=1)
        if self._processor is not None:
            self._processor.on_end(record)

    def shutdown(self, timeout_seconds: float = 5.0) -> None:
        if self._processor is not None:
            self._processor.shutdown(timeout_seconds)


def _error_code(exc: BaseException) -> str:
    """Safe code from the exception TYPE only; never its message."""

    try:
        from netra_api.platform.errors import NetraError, error_code_for

        if isinstance(exc, NetraError):
            return error_code_for(exc).lower()
    except Exception:  # pragma: no cover - defensive
        pass
    return "internal_error"


DISABLED_TRACER = Tracer(None, TracingDiagnostics())


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TracingMode = Literal["off", "local", "ax"]

LANGSMITH_ENV_FLAGS = ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGCHAIN_TRACING")


def disable_langsmith_export(environ: Any) -> list[str]:
    """Force LangSmith/LangChain tracing export off for this process.

    langchain-core pulls the langsmith package in transitively; the package
    stays installed, but no second tracing destination may be active. Returns
    the flags that were previously enabled so composition can report them.
    """

    previously_enabled = [name for name in LANGSMITH_ENV_FLAGS if str(environ.get(name, "")).lower() in ("1", "true", "yes")]
    for name in LANGSMITH_ENV_FLAGS:
        environ[name] = "false"
    return previously_enabled


def build_tracer(
    mode: TracingMode,
    *,
    exporter: Optional[SpanExporter] = None,
    settings: ExportSettings = ExportSettings(),
    service_name: str = "netra-api",
) -> Tracer:
    """Create the process tracer. Failure to configure never prevents boot.

    - ``off``: no spans are created (the measured baseline).
    - ``local``: spans export to the supplied exporter (tests, fixture
      evaluation manifests), defaulting to an in-memory exporter.
    - ``ax``: requires an AX/OTLP exporter built from reviewed OpenTelemetry
      pins. None exists in this build, so this records a configuration error
      and returns a disabled tracer rather than failing the process.
    """

    diagnostics = TracingDiagnostics()
    if mode == "off":
        return Tracer(None, diagnostics, service_name=service_name)
    if mode == "ax" and exporter is None:
        diagnostics.add(configuration_errors=1)
        diagnostics.last_error_code = "ax_exporter_unavailable"
        logger.warning("AX tracing requested but no reviewed exporter is available; tracing disabled")
        return Tracer(None, diagnostics, service_name=service_name)
    processor = BatchSpanProcessor(exporter or InMemorySpanExporter(), diagnostics, settings)
    return Tracer(processor, diagnostics, service_name=service_name)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
