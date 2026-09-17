import json
import logging
from io import StringIO

import pytest

from netra_api.config import Settings
from netra_api.platform.observability import (
    JsonFormatter,
    bind_context,
    configure_observability,
    finished_spans,
    increment,
    log_event,
    metrics_snapshot,
    observe,
    reset_metrics,
    start_span,
)


@pytest.fixture(autouse=True)
def clean_observability():
    configure_observability(metrics_enabled=True, tracing_enabled=True)
    reset_metrics()
    yield
    reset_metrics()


def test_json_logs_include_context_and_filter_sensitive_payloads():
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("test-observability")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    with bind_context(request_id="request-1", correlation_id="corr-1", job_id="job-1"):
        log_event(logger, "retrieval_completed", component="retrieval", duration_ms=4,
                  account_id="account-1", api_key="secret", text="document text",
                  vector=[1.0])
    payload = json.loads(stream.getvalue())
    assert payload["event"] == "retrieval_completed"
    assert payload["request_id"] == "request-1"
    assert payload["duration_ms"] == 4
    assert "api_key" not in payload and "text" not in payload and "vector" not in payload


def test_metrics_have_expected_low_cardinality_labels_and_timing():
    increment("netra_retrieval_requests_total")
    increment("netra_retrieval_provider_duration_seconds", provider="fts")
    observe("netra_retrieval_duration_seconds", 0.25)
    snapshot = metrics_snapshot()
    assert "netra_retrieval_requests_total" in snapshot["counters"]
    assert "netra_retrieval_provider_duration_seconds" in snapshot["counters"]
    assert "netra_retrieval_duration_seconds" in snapshot["histograms"]
    assert all(len(labels) <= 1 for labels in snapshot["counters"]["netra_retrieval_provider_duration_seconds"])


def test_disabled_telemetry_does_not_record_or_change_application_flow():
    configure_observability(metrics_enabled=False, tracing_enabled=False)
    increment("netra_retrieval_requests_total")
    with start_span("retrieval", document_text="must-not-be-recorded"):
        pass
    assert metrics_snapshot() == {"counters": {}, "histograms": {}}


def test_span_records_failure_without_swallowing_exception():
    with pytest.raises(ValueError, match="provider failure"):
        with start_span("pinecone_query", provider="pinecone") as span:
            raise ValueError("provider failure")
    assert span.status == "error"
    assert span.error_type == "ValueError"
    assert finished_spans()[-1].name == "pinecone_query"
