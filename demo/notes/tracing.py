"""Sanitized traces of a run, exported to Arize AX in the background.

A run's history is already an append-only record of what happened (input, evidence, drafts, gate
verdicts, failures), so a trace is a view of it: one span per record, built only from an allowlist
of fields. The private answer key, the Tutor's draft, tokens and anything that looks like a secret
never leave the database. Passage text is not exported, only which passages (file and position).

Export must never slow or break a student's answer: `submit` only queues a run id (bounded; when
full, the oldest is dropped and counted), a background thread builds and sends batches, and every
failure is counted, not raised. `stats()` reports what was sent, failed and dropped, so telemetry
loss is visible rather than silent.

Sends OTLP/HTTP JSON with httpx (already installed; no new dependency). The endpoint and headers
follow Arize's documented OTLP settings but have NOT been verified against a real AX project
from here: until that is done the exporter is tested against a fake receiver only.
"""
from __future__ import annotations

import collections
import json
import os
import re
import threading
import time
from typing import Any, Callable

MAX_TEXT = 600
ARIZE_ENDPOINT = "https://otlp.arize.com/v1/traces"

# Only these fields of each record kind are ever exported. Unlisted kinds (answer_key, tutor_draft,
# question, handoff...) are not exported at all.
_ALLOWED: dict[str, tuple[str, ...]] = {
    "input": ("text",),
    "draft": ("action", "text", "cited_evidence_ids"),
    "verdict": ("status", "objections"),
    "failure": ("kind",),
    "public_turn": ("explanation",),
}

_SECRETS = re.compile(r"(nt_[A-Za-z0-9_\-]{8,}|sk-[A-Za-z0-9_\-]{8,}|Bearer\s+\S+|api[_-]?key\S*\s*[:=]\s*\S+)", re.I)


def scrub(value: Any) -> Any:
    """Redact secret-looking strings and cap the length of text, recursively."""
    if isinstance(value, str):
        text = _SECRETS.sub("[redacted]", value)
        return text if len(text) <= MAX_TEXT else text[:MAX_TEXT] + "..."
    if isinstance(value, list):
        return [scrub(v) for v in value[:20]]
    if isinstance(value, dict):
        return {str(k): scrub(v) for k, v in list(value.items())[:20]}
    return value


def run_to_trace(store, run_id: str) -> dict[str, Any] | None:
    """{trace_id, spans:[{name, start, end, attributes}]} for one run, or None if it is unknown."""
    try:
        state = store.get_state(run_id)
    except Exception:                                    # noqa: BLE001 - an unknown run has no trace
        return None
    versions = store.replay(run_id)
    started = versions[0].created_at if versions else time.time()
    spans = []
    previous = started
    for v in versions:
        attrs: dict[str, Any] = {"record.kind": v.kind, "record.produced_by": v.produced_by, "record.seq": v.seq}
        if v.kind == "evidence":
            attrs["evidence.passages"] = [f'{p.get("doc")}#{p.get("ordinal")}' for p in v.payload.get("passages", [])]
        else:
            for field in _ALLOWED.get(v.kind, ()):
                if field in v.payload:
                    attrs[f"record.{field}"] = scrub(v.payload[field])
        if v.kind in _ALLOWED or v.kind == "evidence":
            spans.append({"name": v.kind, "start": previous, "end": v.created_at, "attributes": attrs})
        previous = v.created_at
    return {"trace_id": run_id, "state": state.value, "start": started, "end": previous, "spans": spans}


def otlp_payload(traces: list[dict[str, Any]], service: str = "netra-notes", project: str = "netra-notes") -> dict:
    """OTLP/HTTP JSON for a batch of traces."""
    import hashlib

    def attr(k, v):
        if isinstance(v, bool):
            return {"key": k, "value": {"boolValue": v}}
        if isinstance(v, int):
            return {"key": k, "value": {"intValue": str(v)}}
        if isinstance(v, float):
            return {"key": k, "value": {"doubleValue": v}}
        return {"key": k, "value": {"stringValue": v if isinstance(v, str) else json.dumps(v)}}

    def nanos(t: float) -> str:
        return str(int(t * 1e9))

    spans = []
    for t in traces:
        trace_id = hashlib.sha256(t["trace_id"].encode()).hexdigest()[:32]
        root_id = hashlib.sha256(("root" + t["trace_id"]).encode()).hexdigest()[:16]
        spans.append({"traceId": trace_id, "spanId": root_id, "name": "notes.run",
                      "startTimeUnixNano": nanos(t["start"]), "endTimeUnixNano": nanos(t["end"]),
                      "attributes": [attr("run.state", t["state"]), attr("openinference.span.kind", "CHAIN")]})
        for i, s in enumerate(t["spans"]):
            spans.append({"traceId": trace_id, "parentSpanId": root_id,
                          "spanId": hashlib.sha256(f'{t["trace_id"]}:{i}'.encode()).hexdigest()[:16],
                          "name": s["name"], "startTimeUnixNano": nanos(s["start"]),
                          "endTimeUnixNano": nanos(s["end"]),
                          "attributes": [attr(k, v) for k, v in s["attributes"].items()]})
    return {"resourceSpans": [{
        "resource": {"attributes": [attr("service.name", service), attr("model_id", project)]},
        "scopeSpans": [{"scope": {"name": "netra.notes"}, "spans": spans}]}]}


class HttpTransport:
    """POSTs a payload with a short timeout. Raises on any non-2xx so the exporter counts a failure."""

    def __init__(self, endpoint: str, headers: dict[str, str], timeout: float = 5.0) -> None:
        self.endpoint, self.headers, self.timeout = endpoint, headers, timeout

    def __call__(self, payload: dict) -> None:
        import httpx
        r = httpx.post(self.endpoint, json=payload, headers=self.headers, timeout=self.timeout)
        r.raise_for_status()


class Exporter:
    def __init__(self, load: Callable[[str], dict | None], transport: Callable[[dict], None], *,
                 batch_size: int = 10, max_queue: int = 200, interval: float = 2.0,
                 project: str = "netra-notes") -> None:
        self._load, self._send = load, transport
        self.batch_size, self.interval, self.project = batch_size, interval, project
        self._queue: collections.deque[str] = collections.deque()
        self._max_queue, self._lock = max_queue, threading.Lock()
        self.sent = self.failed = self.dropped = 0

    def submit(self, run_id: str) -> None:
        """Queue a run id. Never blocks, never raises."""
        with self._lock:
            if len(self._queue) >= self._max_queue:
                self._queue.popleft()
                self.dropped += 1
            self._queue.append(run_id)

    def flush(self) -> int:
        """Send everything queued, in batches. Returns how many traces were sent."""
        total = 0
        while True:
            with self._lock:
                batch = [self._queue.popleft() for _ in range(min(self.batch_size, len(self._queue)))]
            if not batch:
                return total
            try:
                traces = [t for t in (self._load(r) for r in batch) if t]
                if traces:
                    self._send(otlp_payload(traces, project=self.project))
                self.sent += len(traces)
                total += len(traces)
            except Exception:                            # noqa: BLE001 - telemetry must not raise
                self.failed += len(batch)

    def run_forever(self, stop: threading.Event) -> None:
        while not stop.wait(self.interval):
            self.flush()
        self.flush()

    def stats(self) -> dict[str, int]:
        return {"queued": len(self._queue), "sent": self.sent, "failed": self.failed, "dropped": self.dropped}


def from_env(load: Callable[[str], dict | None], env: dict[str, str] | None = None) -> Exporter | None:
    """An exporter if ARIZE_SPACE_ID and ARIZE_API_KEY are set, else None (tracing off)."""
    env = os.environ if env is None else env
    space, key = env.get("ARIZE_SPACE_ID", "").strip(), env.get("ARIZE_API_KEY", "").strip()
    if not (space and key):
        return None
    project = env.get("ARIZE_PROJECT_NAME", "").strip() or "netra-notes"
    return Exporter(load, HttpTransport(env.get("ARIZE_OTLP_ENDPOINT", ARIZE_ENDPOINT),
                                        {"space_id": space, "api_key": key}), project=project)
