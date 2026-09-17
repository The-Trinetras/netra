# Netra observability foundation

Netra's local observability foundation is dependency-free and exporter-neutral.
It provides the stable application boundary for later OpenTelemetry and
Prometheus exporters without requiring a collector or metrics server in local
development or tests.

## Logging

`configure_logging(log_format="json")` installs one JSON stream handler. Each
event contains an ISO-8601 timestamp, level, event name, component, and any
bound request/correlation/job context. Safe diagnostic fields include IDs,
provider, operation, status, duration, and error type. Document text,
passages, vectors, credentials, tokens, and authorization material are
filtered before formatting.

Important event names include `retrieval_started`,
`retrieval_provider_failed`, `retrieval_completed`, `ingestion_stage_started`,
`ingestion_stage_failed`, `ingestion_stage_completed`, `job_claimed`,
`job_completed`, `job_retry_scheduled`, and `outbox_event_processed`.

## Tracing

`start_span` records bounded local spans for retrieval providers, RRF,
canonical evidence resolution, reranking, ingestion stages, and job
execution. Spans carry low-cardinality operation/provider and safe IDs only.
The interface is intentionally compatible with a future OpenTelemetry SDK;
no exporter or collector is required today. Exceptions mark spans as errors
and are re-raised unchanged.

## Metrics

Counters and histograms are collected in-process through `metrics_snapshot`.
Latency histograms use seconds with fixed cumulative buckets of 5ms, 10ms,
25ms, 50ms, 100ms, 250ms, 500ms, 1s, 2s, 5s, 10s, and 30s, plus count/sum.
Current metric names are:

- `netra_retrieval_requests_total`, `netra_retrieval_failures_total`,
  `netra_retrieval_duration_seconds`,
  `netra_retrieval_provider_duration_seconds`
- `netra_ingestion_stage_total`, `netra_ingestion_failures_total`,
  `netra_ingestion_stage_duration_seconds`
- `netra_jobs_claimed_total`, `netra_jobs_completed_total`,
  `netra_jobs_failed_total`, `netra_jobs_retried_total`,
  `netra_job_duration_seconds`
- `netra_outbox_events_claimed_total`,
  `netra_outbox_events_processed_total`,
  `netra_outbox_events_failed_total`,
  `netra_outbox_dispatch_duration_seconds`
- `netra_provider_failures_total`

Labels are bounded: provider, operation, stage, status, job type, and event
type. Questions, document IDs, account IDs, job IDs, and evidence text are
never metric labels.

## Configuration and future boundary

`observability_enabled`, `log_format`, `tracing_enabled`, and
`metrics_enabled` are runtime settings. Logging is JSON by default; metrics
and tracing are local in-process signals and do not require Prometheus,
Grafana, Jaeger, Tempo, CloudWatch, or an OpenTelemetry Collector. A future
API may expose the metrics snapshot through its existing HTTP boundary, and a
future deployment may attach approved OpenTelemetry/Prometheus exporters.
