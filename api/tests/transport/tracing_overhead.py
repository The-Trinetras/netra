"""Measure server-side response overhead of tracing: off vs local exporter vs stalled exporter.

LABELLED FIXTURE MEASUREMENT (not collected by pytest). Drives real M1
transport/session/navigation code with in-memory repositories and the Ohm
fixture. Measures in-process time from pushing a navigation command to the
correlated response, plus event-loop lag while requests run. It does not
measure network, AX ingestion, client STOP-to-silence (M5) or production load.

Run from the repository root:

    python api/tests/transport/tracing_overhead.py [requests_per_mode]
"""

from __future__ import annotations

import asyncio
import os
import statistics
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(HERE, "..", "..", "src")]

from netra_api.platform.tracing import ExportSettings, InMemorySpanExporter  # noqa: E402
from netra_api.transport.websocket.endpoint import serve  # noqa: E402
from ohm_fixture import FakeSocket, build_journey, envelope, wait_for  # noqa: E402

SETTINGS = ExportSettings(max_queue_size=2048, max_batch_size=128, schedule_delay_seconds=0.05, export_timeout_seconds=0.5)


class StalledExporter:
    def __init__(self) -> None:
        self.release = threading.Event()

    def export(self, batch):
        self.release.wait(60)
        return "success"

    def shutdown(self):
        self.release.set()


def pct(values, p):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(p / 100 * (len(ordered) - 1))))]


async def lag_sampler(stop: asyncio.Event, samples: list[float]) -> None:
    """Gap between consecutive event-loop turns (ms): how long anything blocked the loop."""

    last = time.perf_counter()
    while not stop.is_set():
        await asyncio.sleep(0)
        now = time.perf_counter()
        samples.append((now - last) * 1000)
        last = now


async def wait_response(socket, rid: str, kind: str = "session.snapshot") -> None:
    """Event-driven wait (no polling sleep), so timer granularity does not dominate."""

    while not any(m["request_id"] == rid and m["type"] == kind for m in socket.texts):
        socket.new_message.clear()
        await asyncio.wait_for(socket.new_message.wait(), 5)


async def run_mode(name: str, exporter, requests: int) -> dict:
    journey = await build_journey(span_exporter=exporter, export_settings=SETTINGS)
    socket = FakeSocket()
    task = asyncio.ensure_future(serve(socket, journey.services, journey.composition.verifier))
    await wait_for(lambda: socket.accepted)
    stop = asyncio.Event()
    lag: list[float] = []
    sampler = asyncio.ensure_future(lag_sampler(stop, lag))
    latencies: list[float] = []
    version = 10
    for index in range(requests):
        command = "next" if index % 2 == 0 else "previous"
        message = envelope("navigation.command", {"command": command, "expected_session_version": version})
        rid = message["request_id"]
        started = time.perf_counter()
        socket.push(message)
        await wait_response(socket, rid)
        latencies.append((time.perf_counter() - started) * 1000)
        version = next(m for m in socket.texts if m["request_id"] == rid and m["type"] == "session.snapshot")["payload"]["session_version"]
        socket.texts.clear()
    stop_message = envelope("navigation.command", {"command": "stop", "expected_session_version": version})
    started = time.perf_counter()
    socket.push(stop_message)
    await wait_response(socket, stop_message["request_id"])
    stop_ms = (time.perf_counter() - started) * 1000
    stop.set()
    await sampler
    socket.disconnect()
    await task
    shutdown_started = time.perf_counter()
    journey.composition.shutdown_timeout_seconds = 1.0
    journey.composition.shutdown()
    shutdown_ms = (time.perf_counter() - shutdown_started) * 1000
    if isinstance(exporter, StalledExporter):
        exporter.release.set()
    diag = journey.composition.telemetry_diagnostics()
    return {
        "mode": name,
        "n": len(latencies),
        "p50_ms": round(statistics.median(latencies), 3),
        "p95_ms": round(pct(latencies, 95), 3),
        "max_ms": round(max(latencies), 3),
        "stop_ms": round(stop_ms, 3),
        "loop_lag_p95_ms": round(pct(lag, 95), 3) if lag else None,
        "loop_lag_max_ms": round(max(lag), 3) if lag else None,
        "shutdown_ms": round(shutdown_ms, 1),
        "spans_created": diag.get("created", 0),
        "spans_exported": diag.get("exported", 0),
        "spans_lost": sum(diag.get(k, 0) for k in ("dropped_queue_full", "dropped_after_failure", "dropped_at_shutdown")),
        "final_flush": diag.get("final_flush"),
    }


async def main(requests: int) -> None:
    import logging

    logging.getLogger("netra.tracing").setLevel(logging.ERROR)  # keep rate-limited loss warnings out of the table
    await run_mode("warmup", None, 50)
    for name, exporter in (("off", None), ("local", InMemorySpanExporter()), ("stalled", StalledExporter()), ("off", None), ("local", InMemorySpanExporter())):
        print(await run_mode(name, exporter, requests))


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 300))
