"""Tracing overhead and export-failure behaviour, measured without a database.

Not a pytest module. Run from the repository root:

    PYTHONPATH=api/src python api/tests/perf/bench_tracing.py --label baseline --out <dir>

Measures, for exporters that are disabled / healthy / stalled / slow / failing:
- the response-path cost of one span (start, five allowlisted attributes, end);
- the worst single span on the response path (must never wait for export);
- what the collector actually received versus what the diagnostics claim
  (duplicates, spans counted lost but delivered, backlog left behind).

The exporters are in-process stand-ins; this is not a measurement of any
AX/OTLP collector (none is implemented in this build).
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from netra_api.platform.tracing import ExportSettings, InMemorySpanExporter, build_tracer


class Recording:
    """Counts every span the 'collector' receives, including repeats."""

    def __init__(self, delay_seconds: float = 0.0, fail: bool = False, stall: threading.Event | None = None) -> None:
        self.delay, self.fail, self.stall = delay_seconds, fail, stall
        self.received: Counter[str] = Counter()
        self.calls = 0
        self._lock = threading.Lock()

    def export(self, batch):
        with self._lock:
            self.calls += 1
        if self.stall is not None:
            self.stall.wait()
        if self.delay:
            time.sleep(self.delay)
        if self.fail:
            raise ConnectionError("collector unreachable")
        with self._lock:
            self.received.update(span.span_id for span in batch)
        return "success"

    def shutdown(self) -> None:
        pass


def hot_path(tracer, spans: int, paced: bool = False) -> list[float]:
    """Burst mode ends spans back to back (worst case for the queue); paced mode
    ends ten spans every 5 ms (~2000 spans/s, far above a study session's rate)."""

    costs = []
    for index in range(spans):
        if paced and index and index % 10 == 0:
            time.sleep(0.005)
        started = time.perf_counter()
        with tracer.span("netra.tool", netra_tool="search_sources", netra_request_id=f"r{index}") as span:
            span.set(netra_tool_status="ok", netra_evidence_count=3, netra_duration_ms=1.5)
        costs.append((time.perf_counter() - started) * 1e6)
    return costs


def pool_backlog(tracer) -> int:
    processor = tracer._processor
    return processor._call_pool._work_queue.qsize() if processor is not None else 0


def run(name: str, exporter, settings: ExportSettings, spans: int, settle_seconds: float, release=None,
        paced: bool = False) -> dict:
    tracer = build_tracer("off" if exporter is None else "local", exporter=exporter, settings=settings)
    costs = hot_path(tracer, spans, paced)
    time.sleep(settle_seconds)
    backlog_before_shutdown = pool_backlog(tracer)
    if release is not None:
        release.set()
    started = time.perf_counter()
    tracer.shutdown(settings.export_timeout_seconds + 1.0)
    shutdown_ms = (time.perf_counter() - started) * 1000
    time.sleep(settle_seconds)  # let any abandoned export call finish and deliver
    diag = tracer.diagnostics.snapshot()
    ordered = sorted(costs)
    result = {
        "condition": name, "spans": spans,
        "span_us": {"median": statistics.median(ordered), "p99": ordered[int(0.99 * (len(ordered) - 1))],
                    "max": ordered[-1], "mean": statistics.fmean(ordered)},
        "shutdown_ms": round(shutdown_ms, 1),
        "pool_backlog_before_shutdown": backlog_before_shutdown,
        "diagnostics": {k: diag[k] for k in ("created", "ended", "enqueued", "exported", "export_attempts",
                                             "export_timeouts", "dropped_queue_full", "dropped_after_failure",
                                             "dropped_at_shutdown", "final_flush", "last_error_code")},
    }
    if isinstance(exporter, Recording):
        received = exporter.received
        claimed_lost = diag["dropped_queue_full"] + diag["dropped_after_failure"] + diag["dropped_at_shutdown"]
        result["collector"] = {
            "export_calls": exporter.calls,
            "distinct_spans_received": len(received),
            "total_span_deliveries": sum(received.values()),
            "duplicate_deliveries": sum(received.values()) - len(received),
            "claimed_lost": claimed_lost,
            "delivered_but_counted_lost": max(0, len(received) - diag["exported"]),
        }
    elif isinstance(exporter, InMemorySpanExporter):
        result["collector"] = {"distinct_spans_received": len({s.span_id for s in exporter.spans}),
                               "total_span_deliveries": len(exporter.spans)}
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--label", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--spans", type=int, default=5000)
    parser.add_argument("--rounds", type=int, default=5, help="repeat the overhead conditions to show variance")
    args = parser.parse_args(argv)

    # Short timeouts keep the run quick; the ratios are what matter.
    fast = ExportSettings(max_queue_size=2048, max_batch_size=128, schedule_delay_seconds=0.05,
                          export_timeout_seconds=0.2, max_retries=2, retry_backoff_seconds=0.05)
    import logging

    logging.getLogger("netra.tracing").setLevel(logging.ERROR)  # loss is reported in the table instead
    stall = threading.Event()
    results = []
    for round_number in range(args.rounds):
        results += [
            run(f"disabled#r{round_number}", None, fast, args.spans, 0.3),
            run(f"healthy-burst#r{round_number}", Recording(), fast, args.spans, 0.5),
        ]
    results += [
        run("healthy-paced", Recording(), fast, 2000, 0.5, paced=True),
        # slower than the per-call timeout but not hung: every attempt eventually completes
        run("slow-exporter-0.3s", Recording(delay_seconds=0.3), fast, 600, 3.0),
        run("failing-exporter", Recording(fail=True), fast, args.spans, 1.0),
        # hung until shutdown, then released: models a collector that answers late
        run("stalled-then-released", Recording(stall=stall), fast, args.spans, 2.0, release=stall),
    ]
    payload = {"label": args.label, "captured_at": datetime.now(timezone.utc).isoformat(),
               "python": sys.version.split()[0], "settings": vars(fast) if hasattr(fast, "__dict__") else str(fast),
               "results": results}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"tracing-{args.label}.json"
    target.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"wrote {target}")
    for r in results:
        c = r.get("collector", {})
        print(f"{r['condition']:24} span median={r['span_us']['median']:.1f}us p99={r['span_us']['p99']:.1f}us "
              f"max={r['span_us']['max']:.0f}us backlog={r['pool_backlog_before_shutdown']} "
              f"exported={r['diagnostics']['exported']} lost={r['diagnostics']['dropped_queue_full'] + r['diagnostics']['dropped_after_failure'] + r['diagnostics']['dropped_at_shutdown']} "
              f"received={c.get('distinct_spans_received')} dup={c.get('duplicate_deliveries')} "
              f"delivered_but_counted_lost={c.get('delivered_but_counted_lost')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
