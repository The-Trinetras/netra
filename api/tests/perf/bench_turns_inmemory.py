"""Netra's own per-turn overhead through the real M1 transport, with in-memory fixtures.

Not a pytest module. Run from the repository root:

    PYTHONPATH="api/src;api/tests/transport" python api/tests/perf/bench_turns_inmemory.py --label baseline --out <dir>

LABELLED FIXTURE MEASUREMENT. Real: M1 transport dispatcher, session service,
router, Coordinator engine (LangGraph node or direct), tool gateway, evidence
ledger, Tutor gateway validation, speech framing/fencing. Fixture doubles:
persistence (in-memory), M2/M3 services, the Tutor runner, the model (scripted,
zero latency unless --model-delay-ms) and the synthesizer. Numbers are Netra's
CPU/orchestration cost per journey, not database, provider or network latency;
the database-backed harness is api/tests/perf/run_journeys.py.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from netra_api.platform.tracing import ExportSettings, InMemorySpanExporter
from netra_api.session.modes import InteractionMode
from netra_api.session.state import ActiveLessonRef, PendingQuestionRef
from netra_api.transport.websocket.endpoint import serve

from ohm_fixture import (  # type: ignore[import-not-found]
    AXES,
    Q01,
    FakeSocket,
    FixtureSynthesizer,
    ScriptedModel,
    build_journey,
    envelope,
    fid,
    final,
    initial_state,
    tools,
    wait_for,
)

EXPORT = ExportSettings(max_queue_size=4096, max_batch_size=128, schedule_delay_seconds=0.05)


class TimedSocket(FakeSocket):
    def __init__(self) -> None:
        super().__init__()
        self.stamped: list[tuple[float, object]] = []

    async def send_text(self, text: str) -> None:
        self.stamped.append((time.perf_counter(), json.loads(text)))
        await super().send_text(text)

    async def send_bytes(self, data: bytes) -> None:
        self.stamped.append((time.perf_counter(), data))
        await super().send_bytes(data)

    def for_request(self, request_id: str, since: float) -> list[tuple[float, dict]]:
        return [(t, m) for t, m in self.stamped if t >= since and isinstance(m, dict) and m["request_id"] == request_id]

    def audio_since(self, since: float) -> list[float]:
        return [t for t, m in self.stamped if t >= since and isinstance(m, bytes)]


def repair_script():
    return [
        tools(("search_sources", {"query": "this line constant resistance"}), requirements=AXES),
        tools(("describe_figure", {"figure_index": 1}), assessments=[
            {"requirement_id": "x-axis", "status": "missing", "evidence_id": "ev-passage-b12", "gap": "axes not established from text"},
            {"requirement_id": "y-axis", "status": "missing", "evidence_id": "ev-passage-b12", "gap": "axes not established from text"}]),
        final({"action": "delegate_to_tutor",
               "assessments": [
                   {"requirement_id": "x-axis", "status": "supported", "evidence_id": "ev-fig02", "observation_label": "x-axis: current (A)"},
                   {"requirement_id": "y-axis", "status": "supported", "evidence_id": "ev-fig02", "observation_label": "y-axis: voltage (V)"}],
               "tutor": {"mode": "explain", "learning_goal": "Connect the graph and table to constant resistance.",
                         "evidence_ids": ["ev-fig02", "ev-passage-b12"]}}),
    ]


def missing_script():
    return [
        tools(("search_sources", {"query": "transistor amplifier gain"}),
              requirements=[{"requirement_id": "amp", "description": "how a transistor amplifies"}]),
        final({"action": "state_gap", "text": "Your material doesn't cover transistors, so I can't answer that from it."}),
    ]


def answer_script():
    return [
        tools(("search_sources", {"query": "table rows"}), requirements=[{"requirement_id": "rows", "description": "table rows"}]),
        final({"action": "delegate_to_tutor",
               "assessments": [{"requirement_id": "rows", "status": "supported", "evidence_id": "ev-table-tbl01"}],
               "tutor": {"mode": "evaluate_answer", "learning_goal": "Evaluate the answer.", "target_concept_ids": ["ohms-law"],
                         "evidence_ids": ["ev-table-tbl01"]}}),
    ]


def turn(utterance: str, version: int = 10, request_id=None) -> dict:
    return envelope("turn.submit", {"utterance": utterance, "input_mode": "keyboard", "transcript_status": "final",
                                    "expected_session_version": version}, request_id=request_id)


async def open_socket(journey):
    socket = TimedSocket()
    task = asyncio.ensure_future(serve(socket, journey.services, journey.composition.verifier))
    await wait_for(lambda: socket.accepted)
    return socket, task


async def close_socket(socket, task) -> None:
    socket.disconnect()
    await asyncio.wait_for(task, 5)


async def settle(socket: TimedSocket, request_id: str, since: float, until: tuple[str, ...], timeout: float = 5.0):
    await wait_for(lambda: any(m["type"] in until + ("error",) for _, m in socket.for_request(request_id, since)), timeout)
    await asyncio.sleep(0.01)  # collect trailing frames of the same request
    return socket.for_request(request_id, since)


async def journey_turn(kind: str, arm: str, model_delay: float, exporter) -> dict:
    kwargs: dict = {}
    if kind == "grounded_repair":
        model, utterance, until = ScriptedModel(repair_script(), delay=model_delay), "How does this line show constant resistance?", ("quiz.question",)
    elif kind == "missing_evidence":
        model, utterance, until = ScriptedModel(missing_script(), delay=model_delay), "How does a transistor amplify a signal?", ("session.snapshot",)
        kwargs["retrieval_hits"] = {}
    else:
        model, utterance, until = ScriptedModel(answer_script(), delay=model_delay), "8 volts", ("session.snapshot",)
        kwargs["state"] = initial_state(interaction_mode=InteractionMode.TUTOR_LESSON, active_lesson=ActiveLessonRef(lesson_id=fid("lesson")),
                                        pending_question=PendingQuestionRef(question_id="q01", question_version=1, hints_used=0))
    journey = await build_journey(model=model, span_exporter=exporter, export_settings=EXPORT if exporter else None, **kwargs)
    if kind == "answer_check":
        journey.pending.questions["q01"] = Q01
    if arm == "direct":
        journey.services.coordinator._graph = None
    socket, task = await open_socket(journey)
    message = turn(utterance)
    sent = time.perf_counter()
    socket.push(message)
    frames = await settle(socket, message["request_id"], sent, until if kind != "missing_evidence" else ("response.segment",))
    await close_socket(socket, task)
    journey.composition.shutdown()
    kinds = [m["type"] for _, m in frames]
    first_segment = next((t for t, m in frames if m["type"] == "response.segment"), None)
    return {
        "first_text_ms": ((first_segment or frames[-1][0]) - sent) * 1000,
        "complete_ms": (frames[-1][0] - sent) * 1000,
        "model_calls": model.calls,
        "prompt_chars_total": sum(len(p) for p in model.prompts),
        "prompt_chars_max": max((len(p) for p in model.prompts), default=0),
        "error": "error" in kinds,
        "kinds": kinds,
    }


async def journey_stop() -> dict:
    gate = asyncio.Event()
    journey = await build_journey(speech=True, synthesizer=FixtureSynthesizer(chunks=8, gate=gate))
    socket, task = await open_socket(journey)
    message = envelope("navigation.command", {"command": "next", "navigation_unit": "sentence", "expected_session_version": 10})
    sent = time.perf_counter()
    socket.push(message)
    await wait_for(lambda: socket.audio_since(sent))
    first_audio = socket.audio_since(sent)[0]
    cancel_at = time.perf_counter()
    socket.push(envelope("response.cancel", {"cancel_request_id": message["request_id"], "reason": "user_stop"}))
    await asyncio.sleep(0.02)
    gate.set()  # the provider would have continued; fenced output must not reach the client
    await asyncio.sleep(0.2)
    after = socket.audio_since(cancel_at)
    await close_socket(socket, task)
    return {"first_audio_frame_ms": (first_audio - sent) * 1000, "frames_after_cancel": len(after),
            "error": False, "kinds": []}


async def journey_reconnect(model_delay: float) -> dict:
    model = ScriptedModel(repair_script(), delay=max(model_delay, 0.05))
    journey = await build_journey(model=model)
    socket, task = await open_socket(journey)
    turn_id = uuid4()
    socket.push(turn("How does this line show constant resistance?", request_id=turn_id))
    await wait_for(lambda: model.calls == 1)
    await close_socket(socket, task)
    dropped_at = time.perf_counter()
    socket, task = await open_socket(journey)
    resume = envelope("session.resume", {"last_known_session_version": 10})
    socket.push(resume)
    frames = await settle(socket, resume["request_id"], dropped_at, ("session.snapshot",))
    resumed_at = frames[0][0]
    retry = turn("How does this line show constant resistance?", request_id=turn_id)
    socket.push(retry)
    frames = await settle(socket, retry["request_id"], resumed_at, ("response.segment", "quiz.question"))
    await close_socket(socket, task)
    first = next((t for t, m in frames if m["type"] == "response.segment"), frames[-1][0])
    return {"resume_snapshot_ms": (resumed_at - dropped_at) * 1000, "recover_first_text_ms": (first - dropped_at) * 1000,
            "model_calls_total": model.calls, "error": any(m["type"] == "error" for _, m in frames),
            "kinds": [m["type"] for _, m in frames],
            "segment_text_prefixes": [m["payload"]["text"][:48] for _, m in frames if m["type"] == "response.segment"]}


class CompletionRecordingModel(ScriptedModel):
    """Records each decide() that runs to completion, so a call that outlives its cancelled turn is visible."""

    def __init__(self, steps, *, delay: float) -> None:
        super().__init__(steps, delay=delay)
        self.completed_at: list[float] = []

    async def decide(self, config, prompt, tools):
        decision = await super().decide(config, prompt, tools)
        self.completed_at.append(time.perf_counter())
        return decision


async def journey_disconnect_during(stage: str) -> dict:
    """Drop the connection while the Coordinator model call (or the Tutor run) is in flight and
    record whether that work was cancelled or kept running after its turn was cancelled."""

    if stage == "model":
        model = CompletionRecordingModel(repair_script(), delay=0.1)
        journey = await build_journey(model=model)
    else:
        model = CompletionRecordingModel(repair_script(), delay=0.0)
        journey = await build_journey(model=model, tutor_kwargs={"delay": 0.1})
    socket, task = await open_socket(journey)
    socket.push(turn("How does this line show constant resistance?"))
    if stage == "model":
        await wait_for(lambda: model.calls == 1)
    else:
        await wait_for(lambda: journey.tutor.handoffs)
    await close_socket(socket, task)
    dropped_at = time.perf_counter()
    await asyncio.sleep(0.25)  # longer than the in-flight work would need to finish
    if stage == "model":
        orphaned = sum(1 for t in model.completed_at if t > dropped_at)
    else:
        orphaned = 1 if "q01" in journey.pending.questions else 0  # the cancelled run persisted its question
    return {"orphaned_completions": orphaned, "error": False, "kinds": []}


def summarize(values: list[float]) -> dict:
    ordered = sorted(values)
    out = {"n": len(ordered), "median": round(statistics.median(ordered), 3), "min": round(ordered[0], 3),
           "max": round(ordered[-1], 3)}
    if len(ordered) >= 10:
        out["p90"] = round(ordered[round(0.9 * (len(ordered) - 1))], 3)
    return out


async def run(iterations: int, model_delay: float) -> dict:
    import logging

    logging.getLogger("netra.tracing").setLevel(logging.ERROR)
    raw: dict[str, list[dict]] = {}

    def add(key: str, sample: dict) -> None:
        raw.setdefault(key, []).append(sample)

    for _ in range(3):  # warm-up: imports, LangGraph compile paths, prompt template load
        await journey_turn("grounded_repair", "langgraph", 0.0, None)
    for index in range(iterations):
        arms = ("langgraph", "direct") if index % 2 == 0 else ("direct", "langgraph")  # ABBA against drift
        for arm in arms:
            add(f"grounded_repair|{arm}|tracing-off", await journey_turn("grounded_repair", arm, model_delay, None))
        add("grounded_repair|langgraph|tracing-local", await journey_turn("grounded_repair", "langgraph", model_delay, InMemorySpanExporter()))
        add("missing_evidence|langgraph|tracing-off", await journey_turn("missing_evidence", "langgraph", model_delay, None))
        add("answer_check|langgraph|tracing-off", await journey_turn("answer_check", "langgraph", model_delay, None))
        add("stop_during_audio|-|tracing-off", await journey_stop())
        if index < max(5, iterations // 4):
            add("reconnect_recover|langgraph|tracing-off", await journey_reconnect(model_delay))
        if index < 20:
            add("disconnect_during_model|langgraph|tracing-off", await journey_disconnect_during("model"))
            add("disconnect_during_tutor|langgraph|tracing-off", await journey_disconnect_during("tutor"))
    summary = {}
    for key, samples in raw.items():
        metrics = {m for s in samples for m, v in s.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
        summary[key] = {"errors": sum(1 for s in samples if s["error"]), "outcome_kinds": samples[0]["kinds"],
                        **({"segment_text_prefixes": samples[0]["segment_text_prefixes"]} if "segment_text_prefixes" in samples[0] else {}),
                        **{m: summarize([s[m] for s in samples]) for m in sorted(metrics)}}
    return {"summary": summary, "samples": raw}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--label", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--iterations", type=int, default=40)
    parser.add_argument("--model-delay-ms", type=float, default=0.0)
    args = parser.parse_args(argv)
    result = asyncio.run(run(args.iterations, args.model_delay_ms / 1000))
    payload = {"label": args.label, "captured_at": datetime.now(timezone.utc).isoformat(), "python": sys.version.split()[0],
               "iterations": args.iterations, "model_delay_ms": args.model_delay_ms, **result}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"turns-inmemory-{args.label}.json"
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {target}")
    for key, entry in payload["summary"].items():
        headline = {m: v["median"] for m, v in entry.items() if isinstance(v, dict) and "median" in v}
        print(f"{key:48} errors={entry['errors']} {headline}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
