"""End-to-end spans through the real transport with LABELLED fixtures and local exporters.

Proves span structure, sanitization and response-path isolation for M1's
boundary. It is not AX ingestion evidence: no AX exporter exists in this build.
"""

import asyncio
import dataclasses
import json
import threading
import time

from netra_api.platform.tracing import ExportSettings, InMemorySpanExporter
from netra_api.transport.websocket.endpoint import serve

from ohm_fixture import (
    AXES,
    BLOCKS,
    EVIDENCE,
    FakeSocket,
    ScriptedModel,
    build_journey,
    envelope,
    final,
    tools,
    wait_for,
)

QUESTION = "How does this line show constant resistance, and where does the table show it?"
FAST = ExportSettings(max_queue_size=256, max_batch_size=32, schedule_delay_seconds=0.01, export_timeout_seconds=0.2, max_retries=1, retry_backoff_seconds=0.01)


def _repair_script():
    return [
        tools(("search_sources", {"query": "this line constant resistance"}), requirements=AXES),
        tools(
            ("describe_figure", {"figure_index": 1}),
            assessments=[{"requirement_id": "x-axis", "status": "missing", "evidence_id": "ev-passage-b12"}, {"requirement_id": "y-axis", "status": "missing", "evidence_id": "ev-passage-b12"}],
        ),
        final(
            {
                "action": "delegate_to_tutor",
                "assessments": [
                    {"requirement_id": "x-axis", "status": "supported", "evidence_id": "ev-fig02", "observation_label": "x-axis: current (A)"},
                    {"requirement_id": "y-axis", "status": "supported", "evidence_id": "ev-fig02", "observation_label": "y-axis: voltage (V)"},
                ],
                "tutor": {"mode": "explain", "learning_goal": "Connect graph and table.", "evidence_ids": ["ev-fig02", "ev-passage-b12"]},
            }
        ),
    ]


async def _open(journey):
    socket = FakeSocket()
    task = asyncio.ensure_future(serve(socket, journey.services, journey.composition.verifier))
    await wait_for(lambda: socket.accepted)
    return socket, task


def _turn(version=10):
    return envelope("turn.submit", {"utterance": QUESTION, "input_mode": "voice", "transcript_status": "final", "expected_session_version": version})


async def test_repair_journey_produces_a_connected_sanitized_span_tree():
    exporter = InMemorySpanExporter()
    journey = await build_journey(model=ScriptedModel(_repair_script()), span_exporter=exporter, export_settings=FAST)
    socket, task = await _open(journey)
    message = _turn()
    socket.push(message)
    await wait_for(lambda: socket.of_type("quiz.question"))
    socket.disconnect()
    await task
    journey.composition.shutdown()

    diagnostics = journey.composition.telemetry_diagnostics()
    assert diagnostics["final_flush"] == "complete" and diagnostics["exported"] == diagnostics["ended"]
    assert diagnostics["dropped_queue_full"] == diagnostics["dropped_after_failure"] == 0

    names = [s.name for s in exporter.spans]
    assert names.count("netra.model.decision") == 3 and names.count("netra.tool") == 2
    assert {"netra.request", "netra.coordinator.turn", "netra.tutor.handoff"} <= set(names)
    turn = next(s for s in exporter.spans if s.name == "netra.coordinator.turn")
    request = next(s for s in exporter.spans if s.name == "netra.request" and s.attributes.get("netra.request_id") == message["request_id"])
    assert turn.trace_id == request.trace_id and turn.parent_span_id == request.span_id
    assert all(s.trace_id == request.trace_id for s in exporter.spans if s.name in ("netra.tool", "netra.model.decision", "netra.tutor.handoff"))
    assert turn.attributes["netra.budget.model_decisions_used"] == 4  # includes the Tutor's decision

    event_names = [e.name for s in exporter.spans for e in s.events]
    assert event_names.index("evidence_gap") < event_names.index("action_changed") < event_names.index("evidence_validated")
    gap = next(e for s in exporter.spans for e in s.events if e.name == "evidence_gap")
    assert gap.attributes["netra.requirement"] == "x-axis" and gap.attributes["netra.gap"] == "reported_missing"

    exported = json.dumps([dataclasses.asdict(s) for s in exporter.spans], default=str)
    forbidden = [QUESTION, "2/1 = 4/2", "stays straight", "8 volts"] + [e.text for _, e in EVIDENCE.values()]
    forbidden += [sentence.text for block in BLOCKS for sentence in block.sentences]
    assert not [text for text in forbidden if text in exported]
    assert str(journey.identity.accounts and next(iter(journey.identity.accounts))) not in exported  # no account id


class _StalledExporter:
    def __init__(self):
        self.release = threading.Event()

    def export(self, batch):
        self.release.wait(10)
        return "success"

    def shutdown(self):
        self.release.set()


async def test_stalled_exporter_cannot_delay_responses_stop_or_liveness():
    exporter = _StalledExporter()
    journey = await build_journey(model=ScriptedModel(_repair_script()), span_exporter=exporter, export_settings=FAST)
    socket, task = await _open(journey)
    started = time.perf_counter()
    for version in range(10, 16):
        message = envelope("navigation.command", {"command": "next", "expected_session_version": version})
        socket.push(message)
        await wait_for(lambda: any(m["request_id"] == message["request_id"] for m in socket.texts))
    stop = envelope("navigation.command", {"command": "stop", "expected_session_version": 16})
    socket.push(stop)
    await wait_for(lambda: any(m["request_id"] == stop["request_id"] for m in socket.texts))
    elapsed = time.perf_counter() - started
    assert elapsed < 1.0  # exporter is blocked for 10 s; responses and STOP are not

    from netra_api.transport.http.health import liveness

    assert liveness(journey.composition.registered)["status"] == "ok"
    socket.disconnect()
    await task
    shutdown_started = time.perf_counter()
    journey.composition.shutdown_timeout_seconds = 0.3
    journey.composition.shutdown()
    assert time.perf_counter() - shutdown_started < 1.5
    diagnostics = journey.composition.telemetry_diagnostics()
    assert diagnostics["final_flush"] == "incomplete" and diagnostics["export_timeouts"] >= 1
    exporter.release.set()
