"""Arize AX span exporter: OTLP/HTTP JSON, sent with httpx.

This is the exporter that docs/architecture/arize-ax-integration.md requires and
that tracing.py left open. AX ingests OpenTelemetry over HTTP, and OTLP/HTTP has
a JSON encoding, so the httpx already in the runtime is enough: no OpenTelemetry
pin enters the shared API/worker lock, which runtime-baseline.md keeps with
M1/M2. Same wire format and endpoint as demo/notes/tracing.py.

Only ``SpanRecord``s cross this boundary, and tracing.py has already reduced
them to ALLOWED_ATTRIBUTES, so no student text, evidence text, prompt, answer or
exception message can reach AX through here. This module adds three facts of its
own: the span status, the OpenInference span kind and the AX project name.
Credentials travel in headers only and never enter a payload or a log line.

Called only from the single background export thread, never from request code.
It never raises: every failure becomes an ExportOutcome the processor counts,
so a rejected batch is visible in diagnostics and invisible to the student.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Mapping, Optional

import httpx

from netra_api.platform.tracing import ExportOutcome, SpanRecord

logger = logging.getLogger(__name__)

ARIZE_OTLP_ENDPOINT = "https://otlp.arize.com/v1/traces"

_RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
"""Rate limits and transient server errors: the processor may retry these."""

_OTLP_OK, _OTLP_ERROR = 1, 2


def _value(value: Any) -> dict[str, Any]:
    """One OTLP AnyValue. int64 travels as a string, per the JSON encoding."""

    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, (list, tuple)):
        return {"arrayValue": {"values": [_value(item) for item in value]}}
    return {"stringValue": value if isinstance(value, str) else json.dumps(value)}


def _attributes(attributes: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [{"key": key, "value": _value(value)} for key, value in attributes.items()]


def span_kind(attributes: Mapping[str, Any]) -> str:
    """OpenInference span kind, inferred from facts the span already carries so
    AX groups provider attempts, retrieval and tool calls as what they are."""

    if "llm.model_name" in attributes or "llm.provider" in attributes:
        return "LLM"
    if "netra.evidence_ids" in attributes or "netra.evidence_count" in attributes:
        return "RETRIEVER"
    if "netra.tool" in attributes:
        return "TOOL"
    return "CHAIN"


def otlp_payload(batch: list[SpanRecord], *, service_name: str, project: str) -> dict[str, Any]:
    """OTLP/HTTP JSON for one batch. ``model_id`` is the AX project name."""

    spans: list[dict[str, Any]] = []
    for record in batch:
        attributes = dict(record.attributes)
        attributes["openinference.span.kind"] = span_kind(attributes)
        attributes["netra.span_status"] = record.status
        span: dict[str, Any] = {
            "traceId": record.trace_id,
            "spanId": record.span_id,
            "name": record.name,
            "kind": 1,  # SPAN_KIND_INTERNAL
            "startTimeUnixNano": str(record.start_unix_ns),
            "endTimeUnixNano": str(record.end_unix_ns),
            "attributes": _attributes(attributes),
            # ok, error, cancelled and timeout stay distinguishable: OTLP has one
            # error code, so the exact outcome rides as netra.span_status.
            "status": {"code": _OTLP_OK if record.status == "ok" else _OTLP_ERROR},
        }
        if record.parent_span_id:
            span["parentSpanId"] = record.parent_span_id
        if record.events:
            span["events"] = [
                {"name": event.name, "timeUnixNano": str(event.at_unix_ns), "attributes": _attributes(event.attributes)}
                for event in record.events
            ]
        spans.append(span)
    return {
        "resourceSpans": [
            {
                "resource": {"attributes": _attributes({"service.name": service_name, "model_id": project})},
                "scopeSpans": [{"scope": {"name": "netra"}, "spans": spans}],
            }
        ]
    }


class ArizeSpanExporter:
    """One POST per batch, bounded timeout, no retry of its own.

    Retry, backoff and the rule that a call which may have delivered is never
    resubmitted belong to BatchSpanProcessor; this only reports which kind of
    failure occurred.
    """

    def __init__(
        self,
        *,
        space_id: str,
        api_key: str,
        project: str,
        service_name: str = "netra-api",
        endpoint: str = ARIZE_OTLP_ENDPOINT,
        timeout: float = 5.0,
        client: Optional[httpx.Client] = None,
    ) -> None:
        self._endpoint = endpoint
        self._project = project
        self._service_name = service_name
        self._headers = {"space_id": space_id, "api_key": api_key, "content-type": "application/json"}
        self._client = client if client is not None else httpx.Client(timeout=timeout)

    def export(self, batch: list[SpanRecord]) -> ExportOutcome:
        if not batch:
            return "success"
        payload = otlp_payload(batch, service_name=self._service_name, project=self._project)
        try:
            response = self._client.post(self._endpoint, json=payload, headers=self._headers)
        except httpx.HTTPError:
            # Timeouts and transport errors: an unreachable AX is retryable. The
            # message is not logged; it can carry the endpoint and request body.
            logger.warning("AX export could not reach the endpoint")
            return "retryable_failure"
        except Exception:
            logger.warning("AX export failed before sending")
            return "permanent_failure"
        if 200 <= response.status_code < 300:
            return "success"
        if response.status_code in _RETRYABLE_STATUS:
            logger.warning("AX deferred a span batch (status %s)", response.status_code)
            return "retryable_failure"
        # 401/403 and schema rejections: retrying the same batch cannot fix them.
        logger.warning("AX rejected a span batch (status %s)", response.status_code)
        return "permanent_failure"

    def shutdown(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass


def from_env(service_name: str, environ: Optional[Mapping[str, str]] = None, *, timeout: float = 5.0) -> Optional[ArizeSpanExporter]:
    """An exporter when ARIZE_SPACE_ID and ARIZE_API_KEY are both set, else None.

    These are the names AX documents, and the ones demo/notes/tracing.py and the
    repository .env already use. None means AX is not configured: the caller
    records a configuration error and tracing stays off, never blocking boot and
    never guessing a destination.
    """

    env = os.environ if environ is None else environ
    space_id = (env.get("ARIZE_SPACE_ID") or "").strip()
    api_key = (env.get("ARIZE_API_KEY") or "").strip()
    if not (space_id and api_key):
        return None
    return ArizeSpanExporter(
        space_id=space_id,
        api_key=api_key,
        project=(env.get("ARIZE_PROJECT_NAME") or "").strip() or "netra",
        service_name=service_name,
        endpoint=(env.get("ARIZE_OTLP_ENDPOINT") or "").strip() or ARIZE_OTLP_ENDPOINT,
        timeout=timeout,
    )
