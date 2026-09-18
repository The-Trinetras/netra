"""M2 operational logs and local metrics for ingestion, retrieval and jobs.

Spans are NOT produced here. Tracing uses M1's single boundary,
``netra_api.platform.tracing.Tracer`` (see docs/architecture/arize-ax-integration.md):
one tracer per process, created in composition and injected into services and
worker handlers. ``stage_span`` and ``instrument_stage`` only translate M2
facts into that tracer's allowlisted attributes (``netra.operation``,
``netra.source_version_id``, ``netra.attempt``, ``netra.outcome`` ...).

Logs and counters are best-effort diagnostics. A failure to log or count can
never change ingestion, retrieval or transaction behaviour, and nothing here
performs network I/O.
"""

from __future__ import annotations

import json
import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
from threading import Lock
from typing import Any, Iterator

from netra_api.platform.tracing import DISABLED_TRACER, Tracer

_context: ContextVar[dict[str, str]] = ContextVar("netra_content_log_context", default={})
_metrics_enabled = True
_metric_lock = Lock()
_counters: dict[str, dict[tuple[tuple[str, str], ...], float]] = {}
_histograms: dict[str, dict[tuple[tuple[str, str], ...], dict[str, float]]] = {}
_HISTOGRAM_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0)

# Log field names containing any of these fragments are dropped. Logs carry
# identifiers, counts and codes; never document text, vectors or credentials.
_SENSITIVE = ("key", "token", "password", "secret", "credential", "authorization",
              "embedding", "vector", "document", "passage", "content", "prompt", "text",
              "query", "url")


def configure_metrics(*, enabled: bool = True) -> None:
    """Process-level switch, called once by the API/worker composition root."""

    global _metrics_enabled
    _metrics_enabled = enabled


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


def _safe_fields(values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items()
            if not any(part in key.lower() for part in _SENSITIVE)}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        fields = getattr(record, "netra_fields", {})
        payload = {"timestamp": datetime.now(timezone.utc).isoformat(),
                   "level": record.levelname, "event": getattr(record, "netra_event", record.getMessage()),
                   "component": getattr(record, "netra_component", record.name)}
        payload.update(_safe_fields(current_context()))
        payload.update(_safe_fields(fields))
        if record.exc_info and record.exc_info[0] is not None:
            payload["error_type"] = record.exc_info[0].__name__
        return json.dumps(payload, sort_keys=True, default=str)


def configure_logging(*, log_format: str = "json", level: int = logging.INFO) -> None:
    root = logging.getLogger()
    root.setLevel(level)
    if any(getattr(handler, "_netra_handler", False) for handler in root.handlers):
        return
    handler = logging.StreamHandler()
    handler._netra_handler = True  # type: ignore[attr-defined]
    handler.setFormatter(JsonFormatter() if log_format == "json" else logging.Formatter("%(levelname)s %(message)s"))
    root.addHandler(handler)


def log_event(logger: logging.Logger, event: str, *, component: str, level: int = logging.INFO, **fields: Any) -> None:
    try:
        logger.log(level, event, extra={"netra_event": event, "netra_component": component,
                                       "netra_fields": _safe_fields(fields)})
    except Exception:
        # Logging must never change application behaviour.
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


def tracer_of(owner: Any) -> Tracer:
    """The tracer injected into a service/handler, or the disabled tracer."""

    tracer = getattr(owner, "tracer", None)
    return tracer if isinstance(tracer, Tracer) else DISABLED_TRACER


def _id(value: Any) -> str | None:
    return None if value is None else str(value)


@contextmanager
def stage_span(tracer: Tracer, name: str, *, operation: str,
               source_version_id: Any = None, **attributes: Any) -> Iterator[Any]:
    """Open an M1 span for an M2 operation using only allowlisted attributes.

    Unknown keys are dropped by the tracer's sanitizer before any export;
    passing free text here cannot leak it.
    """

    values: dict[str, Any] = {"netra.operation": operation}
    if source_version_id is not None:
        values["netra.source_version_id"] = _id(source_version_id)
    values.update(attributes)
    with tracer.span(name, **values) as span:
        yield span


def instrument_stage(stage: str):
    """Log, count and trace one async ingestion stage; exceptions propagate unchanged."""

    def decorator(function):
        @wraps(function)
        async def wrapped(self, payload, *args, **kwargs):
            started = time.perf_counter()
            version_id = getattr(payload, "source_version_id", None)
            logger = logging.getLogger(function.__module__)
            log_event(logger, "ingestion_stage_started", component="ingestion",
                      stage=stage, source_version_id=version_id)
            outcome = "failure"
            try:
                with stage_span(tracer_of(self), f"ingestion.{stage}", operation=stage,
                                source_version_id=version_id) as span:
                    result = await function(self, payload, *args, **kwargs)
                    span.set(**{"netra.outcome": "success"})
                outcome = "success"
                return result
            except Exception as exc:
                increment("netra_ingestion_failures_total", stage=stage)
                log_event(logger, "ingestion_stage_failed", component="ingestion", stage=stage,
                          source_version_id=version_id, error_type=type(exc).__name__, level=logging.ERROR)
                raise
            finally:
                elapsed = time.perf_counter() - started
                increment("netra_ingestion_stage_total", stage=stage, status=outcome)
                observe("netra_ingestion_stage_duration_seconds", elapsed, stage=stage)
                log_event(logger, "ingestion_stage_completed", component="ingestion", stage=stage,
                          source_version_id=version_id, outcome=outcome,
                          duration_ms=round(elapsed * 1000, 3))
        return wrapped
    return decorator


__all__ = [
    "JsonFormatter",
    "bind_context",
    "configure_logging",
    "configure_metrics",
    "current_context",
    "increment",
    "instrument_stage",
    "log_event",
    "metrics_snapshot",
    "observe",
    "reset_metrics",
    "stage_span",
    "tracer_of",
]
