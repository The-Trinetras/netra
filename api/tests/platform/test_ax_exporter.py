"""The AX exporter: wire format, outcome mapping and what may never be sent.

Every test here uses a fake httpx transport. A real AX POST is a live check,
recorded separately; a passing mock is not evidence that AX ingested anything.
"""

from __future__ import annotations

import httpx
import pytest

from netra_api.platform.ax_exporter import ArizeSpanExporter, from_env, otlp_payload, span_kind
from netra_api.platform.tracing import ExportSettings, SpanEvent, SpanRecord, build_tracer


def record(**overrides) -> SpanRecord:
    base = dict(
        trace_id="0" * 32,
        span_id="1" * 16,
        parent_span_id=None,
        name="netra.request",
        start_unix_ns=1_700_000_000_000_000_000,
        end_unix_ns=1_700_000_000_500_000_000,
        status="ok",
        attributes={"netra.operation": "turn", "netra.request_id": "req-1"},
        events=(),
    )
    base.update(overrides)
    return SpanRecord(**base)


def exporter_with(handler, **kwargs) -> ArizeSpanExporter:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return ArizeSpanExporter(space_id="space", api_key="key", project="netra-test", client=client, **kwargs)


def capture() -> tuple[list[httpx.Request], ArizeSpanExporter]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"partialSuccess": {}})

    return seen, exporter_with(handler)


def test_the_payload_is_otlp_json_with_the_span_identity_and_timings():
    payload = otlp_payload([record()], service_name="netra-api", project="netra-test")
    resource = payload["resourceSpans"][0]
    spans = resource["scopeSpans"][0]["spans"]
    assert [a["key"] for a in resource["resource"]["attributes"]] == ["service.name", "model_id"]
    assert resource["resource"]["attributes"][1]["value"]["stringValue"] == "netra-test"
    assert len(spans) == 1
    span = spans[0]
    assert span["traceId"] == "0" * 32 and span["spanId"] == "1" * 16
    assert "parentSpanId" not in span
    assert span["startTimeUnixNano"] == "1700000000000000000"
    assert span["endTimeUnixNano"] == "1700000000500000000"
    assert span["status"]["code"] == 1


def test_a_child_span_keeps_its_parent_and_its_events():
    child = record(
        parent_span_id="2" * 16,
        events=(SpanEvent(name="evidence.selected", at_unix_ns=1_700_000_000_100_000_000, attributes={"netra.evidence_count": 3}),),
    )
    span = otlp_payload([child], service_name="netra-api", project="p")["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    assert span["parentSpanId"] == "2" * 16
    assert span["events"][0]["name"] == "evidence.selected"
    assert span["events"][0]["attributes"][0]["value"]["intValue"] == "3"


@pytest.mark.parametrize(
    "status, code",
    [("ok", 1), ("error", 2), ("cancelled", 2), ("timeout", 2)],
)
def test_every_outcome_is_still_distinguishable_although_otlp_has_one_error_code(status, code):
    span = otlp_payload([record(status=status)], service_name="s", project="p")["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    attributes = {a["key"]: a["value"] for a in span["attributes"]}
    assert span["status"]["code"] == code
    assert attributes["netra.span_status"]["stringValue"] == status


def test_values_keep_their_type_and_lists_stay_lists():
    attributes = {
        "netra.replayed": True,
        "netra.session_version": 7,
        "netra.duration_ms": 12.5,
        "netra.evidence_ids": ["e1", "e2"],
    }
    span = otlp_payload([record(attributes=attributes)], service_name="s", project="p")["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    exported = {a["key"]: a["value"] for a in span["attributes"]}
    assert exported["netra.replayed"] == {"boolValue": True}
    assert exported["netra.session_version"] == {"intValue": "7"}
    assert exported["netra.duration_ms"] == {"doubleValue": 12.5}
    assert exported["netra.evidence_ids"]["arrayValue"]["values"] == [{"stringValue": "e1"}, {"stringValue": "e2"}]


def test_the_span_kind_follows_the_facts_the_span_already_carries():
    assert span_kind({"llm.model_name": "openai/gpt-oss-120b"}) == "LLM"
    assert span_kind({"netra.evidence_count": 2}) == "RETRIEVER"
    assert span_kind({"netra.tool": "read_source"}) == "TOOL"
    assert span_kind({"netra.operation": "turn"}) == "CHAIN"


def test_credentials_travel_in_headers_and_never_in_the_payload():
    # Distinctive values: "key" and "space" also occur in OTLP field names.
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    exporter = ArizeSpanExporter(space_id="SPACE-ID-7f3a", api_key="AX-SECRET-9b21", project="p", client=client)
    assert exporter.export([record()]) == "success"
    request = seen[0]
    assert request.headers["space_id"] == "SPACE-ID-7f3a" and request.headers["api_key"] == "AX-SECRET-9b21"
    body = request.content.decode()
    assert "SPACE-ID-7f3a" not in body and "AX-SECRET-9b21" not in body


@pytest.mark.parametrize(
    "status_code, outcome",
    [(200, "success"), (202, "success"), (429, "retryable_failure"), (503, "retryable_failure"),
     (401, "permanent_failure"), (403, "permanent_failure"), (422, "permanent_failure")],
)
def test_http_status_maps_to_the_outcome_the_processor_acts_on(status_code, outcome):
    exporter = exporter_with(lambda request: httpx.Response(status_code, json={}))
    assert exporter.export([record()]) == outcome


def test_an_unreachable_ax_is_retryable_and_never_raises_into_the_processor():
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    assert exporter_with(refuse).export([record()]) == "retryable_failure"


def test_a_timeout_is_retryable_and_never_raises():
    def stall(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    assert exporter_with(stall).export([record()]) == "retryable_failure"


def test_an_empty_batch_sends_nothing():
    seen, exporter = capture()
    assert exporter.export([]) == "success"
    assert seen == []


def test_from_env_needs_both_credentials_and_otherwise_configures_nothing():
    assert from_env("netra-api", {}) is None
    assert from_env("netra-api", {"ARIZE_SPACE_ID": "s"}) is None
    assert from_env("netra-api", {"ARIZE_API_KEY": "k"}) is None
    assert from_env("netra-api", {"ARIZE_SPACE_ID": "  ", "ARIZE_API_KEY": "k"}) is None
    assert from_env("netra-api", {"ARIZE_SPACE_ID": "s", "ARIZE_API_KEY": "k"}) is not None


def test_from_env_defaults_the_project_and_endpoint_but_lets_them_be_set():
    default = from_env("netra-api", {"ARIZE_SPACE_ID": "s", "ARIZE_API_KEY": "k"})
    assert default._project == "netra" and default._endpoint.endswith("/v1/traces")
    chosen = from_env(
        "netra-worker",
        {"ARIZE_SPACE_ID": "s", "ARIZE_API_KEY": "k", "ARIZE_PROJECT_NAME": "netra-eval", "ARIZE_OTLP_ENDPOINT": "https://example.test/v1/traces"},
    )
    assert chosen._project == "netra-eval" and chosen._endpoint == "https://example.test/v1/traces"


def test_ax_mode_builds_the_exporter_from_the_environment(monkeypatch):
    monkeypatch.setenv("ARIZE_SPACE_ID", "space")
    monkeypatch.setenv("ARIZE_API_KEY", "key")
    tracer = build_tracer("ax", settings=ExportSettings(schedule_delay_seconds=0.01))
    try:
        assert tracer.enabled is True
        assert tracer.diagnostics.configuration_errors == 0
    finally:
        tracer.shutdown(1)


def test_a_turn_never_waits_for_the_export_call():
    started = []

    def slow(request: httpx.Request) -> httpx.Response:
        started.append(True)
        raise httpx.ReadTimeout("slow", request=request)

    exporter = exporter_with(slow)
    tracer = build_tracer("local", exporter=exporter, settings=ExportSettings(schedule_delay_seconds=0.01, max_retries=0))
    try:
        with tracer.span("netra.request") as span:
            span.set(netra_operation="turn")
        assert tracer.diagnostics.ended == 1
    finally:
        tracer.shutdown(1)
