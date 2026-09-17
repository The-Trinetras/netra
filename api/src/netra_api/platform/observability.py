"""Dependency-free observability primitives for local and production wiring.

The public shape deliberately mirrors the eventual OpenTelemetry and
Prometheus boundaries without requiring a collector or exporter in tests.
Telemetry is best-effort and never part of application correctness.
"""

from __future__ import annotations

import json
import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import wraps
from threading import Lock
from typing import Any, Iterator


_context: ContextVar[dict[str, str]] = ContextVar("netra_observability_context", default={})
_metrics_enabled = True
_tracing_enabled = True
_metric_lock = Lock()
_counters: dict[str, dict[tuple[tuple[str, str], ...], float]] = {}
_histograms: dict[str, dict[tuple[tuple[str, str], ...], dict[str, float]]] = {}
_finished_spans: list["Span"] = []
_HISTOGRAM_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0)


def configure_observability(*, metrics_enabled: bool = True, tracing_enabled: bool = True) -> None:
    global _metrics_enabled, _tracing_enabled
    _metrics_enabled, _tracing_enabled = metrics_enabled, tracing_enabled


@contextmanager
def bind_context(**values: Any) -> Iterator[dict[str, str]]:
    merged = dict(_context.get())
    merged.update({key: str(value) for key, value in values.items() if value is not None})
    token = _context.set(merged)
    try:
        yield merged
    finally:
        _context.reset(token)


def current_context() -> dict[str, str]:
    return dict(_context.get())


_SENSITIVE = ("key", "token", "password", "secret", "credential", "authorization",
              "embedding", "vector", "document", "passage", "content", "prompt", "text")


def _safe_fields(values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items()
            if not any(part in key.lower() for part in _SENSITIVE)}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        fields = getattr(record, "netra_fields", {})
        payload = {"timestamp": datetime.now(timezone.utc).isoformat(),
                   "level": record.levelname, "event": getattr(record, "netra_event", record.getMessage()),
                   "component": getattr(record, "netra_component", record.name)}
        payload.update(current_context())
        payload.update(_safe_fields(fields))
        if record.exc_info:
            payload["error_type"] = record.exc_info[0].__name__
        return json.dumps(payload, sort_keys=True, default=str)


def configure_logging(*, log_format: str = "json", level: int = logging.INFO) -> None:
    root = logging.getLogger()
    root.setLevel(level)
    if any(getattr(handler, "_netra_handler", False) for handler in root.handlers):
        return
    handler = logging.StreamHandler()
    handler._netra_handler = True
    handler.setFormatter(JsonFormatter() if log_format == "json" else logging.Formatter("%(levelname)s %(message)s"))
    root.addHandler(handler)


def log_event(logger: logging.Logger, event: str, *, component: str, level: int = logging.INFO, **fields: Any) -> None:
    try:
        logger.log(level, event, extra={"netra_event": event, "netra_component": component,
                                       "netra_fields": _safe_fields(fields)})
    except Exception:
        # Logging must never change application behavior.
        return


def _labels(labels: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((key, str(value)) for key, value in labels.items()))


def increment(name: str, amount: float = 1, **labels: Any) -> None:
    if not _metrics_enabled:
        return
    try:
        with _metric_lock:
            key = _labels(labels)
            bucket = _counters.setdefault(name, {})
            bucket[key] = bucket.get(key, 0) + amount
    except Exception:
        return


def observe(name: str, value: float, **labels: Any) -> None:
    if not _metrics_enabled:
        return
    try:
        with _metric_lock:
            key = _labels(labels)
            item = _histograms.setdefault(name, {}).setdefault(
                key, {"count": 0, "sum": 0.0, **{f"bucket_le_{bucket:g}": 0 for bucket in _HISTOGRAM_BUCKETS}})
            item["count"] += 1
            item["sum"] += float(value)
            for bucket in _HISTOGRAM_BUCKETS:
                if value <= bucket:
                    item[f"bucket_le_{bucket:g}"] += 1
    except Exception:
        return


def reset_metrics() -> None:
    with _metric_lock:
        _counters.clear()
        _histograms.clear()


def metrics_snapshot() -> dict[str, Any]:
    with _metric_lock:
        return {"counters": {name: dict(values) for name, values in _counters.items()},
                "histograms": {name: dict(values) for name, values in _histograms.items()}}


@dataclass
class Span:
    name: str
    attributes: dict[str, Any] = field(default_factory=dict)
    start: float = field(default_factory=time.perf_counter)
    status: str = "ok"
    error_type: str | None = None
    duration_ms: float = 0.0

    def record_exception(self, exc: BaseException) -> None:
        self.status, self.error_type = "error", type(exc).__name__

    def __enter__(self) -> "Span":
        return self

    def __exit__(self, exc_type, exc, _tb) -> bool:
        if exc is not None:
            self.record_exception(exc)
        self.duration_ms = (time.perf_counter() - self.start) * 1000
        if _tracing_enabled:
            try:
                _finished_spans.append(self)
                del _finished_spans[:-1000]
            except Exception:
                pass
        return False


@contextmanager
def start_span(name: str, **attributes: Any) -> Iterator[Span]:
    span = Span(name, _safe_fields({key: value for key, value in attributes.items() if value is not None}))
    try:
        yield span
    except Exception as exc:
        span.record_exception(exc)
        raise
    finally:
        span.__exit__(None, None, None)


def finished_spans() -> list[Span]:
    return list(_finished_spans)


def instrument_stage(stage: str):
    """Instrument an async stage while preserving its exception semantics."""
    def decorator(function):
        @wraps(function)
        async def wrapped(self, payload, *args, **kwargs):
            started = time.perf_counter()
            version_id = getattr(payload, "source_version_id", None)
            log_event(logging.getLogger(function.__module__), "ingestion_stage_started",
                      component="ingestion", stage=stage, source_version_id=version_id)
            try:
                with start_span(stage, component="ingestion", operation=stage,
                                source_version_id=version_id):
                    result = await function(self, payload, *args, **kwargs)
                increment("netra_ingestion_stage_total", stage=stage, status="success")
                return result
            except Exception as exc:
                increment("netra_ingestion_stage_total", stage=stage, status="failure")
                increment("netra_ingestion_failures_total", stage=stage)
                log_event(logging.getLogger(function.__module__), "ingestion_stage_failed",
                          component="ingestion", stage=stage, source_version_id=version_id,
                          error_type=type(exc).__name__, level=logging.ERROR)
                raise
            finally:
                observe("netra_ingestion_stage_duration_seconds",
                        time.perf_counter() - started, stage=stage)
                log_event(logging.getLogger(function.__module__), "ingestion_stage_completed",
                          component="ingestion", stage=stage, source_version_id=version_id,
                          duration_ms=round((time.perf_counter() - started) * 1000, 3))
        return wrapped
    return decorator
