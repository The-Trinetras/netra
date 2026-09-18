"""M2 operational telemetry: logs, local metrics and spans through M1's tracer."""

import json
import logging
from io import StringIO

import pytest

from netra_api.content.telemetry import (
    JsonFormatter,
    bind_context,
    configure_metrics,
    increment,
    instrument_stage,
    log_event,
    metrics_snapshot,
    observe,
    reset_metrics,
    stage_span,
)
from netra_api.platform.tracing import InMemorySpanExporter, build_tracer


@pytest.fixture(autouse=True)
def clean_metrics():
    configure_metrics(enabled=True)
    reset_metrics()
    yield
    configure_metrics(enabled=True)
    reset_metrics()


def _local_tracer():
    exporter = InMemorySpanExporter()
    return build_tracer("local", exporter=exporter), exporter


def test_json_logs_include_context_and_filter_sensitive_payloads():
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("test-observability")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    with bind_context(request_id="request-1", job_id="job-1"):
        log_event(logger, "retrieval_completed", component="retrieval", duration_ms=4,
                  api_key="secret", text="document text", vector=[1.0], query_text="q")
    payload = json.loads(stream.getvalue())
    assert payload["event"] == "retrieval_completed"
    assert payload["request_id"] == "request-1"
    assert payload["duration_ms"] == 4
    for leaked in ("api_key", "text", "vector", "query_text"):
        assert leaked not in payload


def test_metrics_have_low_cardinality_labels_and_timing():
    increment("netra_retrieval_requests_total")
    increment("netra_provider_failures_total", provider="fts")
    observe("netra_retrieval_duration_seconds", 0.25)
    snapshot = metrics_snapshot()
    assert "netra_retrieval_requests_total" in snapshot["counters"]
    assert "netra_retrieval_duration_seconds" in snapshot["histograms"]
    assert all(len(labels) <= 1 for labels in snapshot["counters"]["netra_provider_failures_total"])


def test_disabled_metrics_record_nothing():
    configure_metrics(enabled=False)
    increment("netra_retrieval_requests_total")
    observe("netra_retrieval_duration_seconds", 0.1)
    assert metrics_snapshot() == {"counters": {}, "histograms": {}}


def test_stage_span_exports_only_allowlisted_attributes():
    tracer, exporter = _local_tracer()
    with stage_span(tracer, "retrieval.fts", operation="retrieval_fts",
                    source_version_id="3f1b0c9e-0000-4000-8000-000000000001",
                    document_text="must not be exported", **{"netra.evidence_count": 2}):
        pass
    tracer.shutdown(2.0)
    [record] = exporter.spans
    assert record.attributes["netra.operation"] == "retrieval_fts"
    assert record.attributes["netra.source_version_id"].startswith("3f1b0c9e")
    assert record.attributes["netra.evidence_count"] == 2
    assert "document_text" not in record.attributes


def test_span_records_failure_without_swallowing_exception():
    tracer, exporter = _local_tracer()
    with pytest.raises(ValueError, match="provider failure"):
        with stage_span(tracer, "retrieval.pinecone", operation="retrieval_pinecone"):
            raise ValueError("provider failure")
    tracer.shutdown(2.0)
    [record] = exporter.spans
    assert record.status == "error"
    assert record.attributes["netra.error_code"] == "internal_error"
    # The exception message never becomes an exported attribute.
    assert "provider failure" not in json.dumps(record.attributes)


class _Payload:
    source_version_id = "3f1b0c9e-0000-4000-8000-000000000002"


class _Stage:
    def __init__(self, tracer, fail=False):
        self.tracer, self.fail = tracer, fail

    @instrument_stage("parse_document")
    async def handle(self, payload):
        if self.fail:
            raise RuntimeError("boom")
        return "done"


async def test_instrument_stage_traces_success_and_failure_through_injected_tracer():
    tracer, exporter = _local_tracer()
    assert await _Stage(tracer).handle(_Payload()) == "done"
    with pytest.raises(RuntimeError):
        await _Stage(tracer, fail=True).handle(_Payload())
    tracer.shutdown(2.0)
    ok, failed = exporter.spans
    assert ok.name == "ingestion.parse_document" and ok.attributes["netra.outcome"] == "success"
    assert failed.status == "error"
    counters = metrics_snapshot()["counters"]["netra_ingestion_stage_total"]
    assert counters[(("stage", "parse_document"), ("status", "success"))] == 1
    assert counters[(("stage", "parse_document"), ("status", "failure"))] == 1


async def test_instrument_stage_without_tracer_is_a_noop_span():
    class Untraced:
        @instrument_stage("embed_text")
        async def handle(self, payload):
            return 1

    assert await Untraced().handle(_Payload()) == 1
