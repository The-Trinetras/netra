"""Unexpected errors are logged by type and stack, never by message.

An exception message can carry student text, provider output or private
answer data (a pydantic ValidationError prints its input values). CLAUDE.md
keeps private answers and rubrics out of ordinary logs, so the dispatcher's
catch-all handlers log the exception type and frames only. The client still
gets the same bounded `INTERNAL_ERROR` error.

LABELLED FIXTURE RUN: scripted model, fixture services, in-memory persistence.
"""

import asyncio
import logging

from netra_api.transport.websocket.endpoint import serve

from ohm_fixture import AXES, FakeSocket, ScriptedModel, build_journey, envelope, final, tools, wait_for

MARKER = "PRIVATE-answer_key-8-volts"


def _script():
    return [
        tools(("search_sources", {"query": "this line constant resistance"}), requirements=AXES),
        final(
            {
                "action": "delegate_to_tutor",
                "assessments": [
                    {"requirement_id": "x-axis", "status": "supported", "evidence_id": "ev-passage-b12"},
                    {"requirement_id": "y-axis", "status": "supported", "evidence_id": "ev-passage-b12"},
                ],
                "tutor": {"mode": "explain", "learning_goal": "Connect the graph and table.", "evidence_ids": ["ev-passage-b12"]},
            }
        ),
    ]


async def _exchange(journey, message):
    socket = FakeSocket()
    task = asyncio.ensure_future(serve(socket, journey.services, journey.composition.verifier))
    await wait_for(lambda: socket.accepted)
    socket.push(message)
    await wait_for(lambda: any(m["request_id"] == message["request_id"] and m["type"] == "error" for m in socket.texts), 3)
    socket.disconnect()
    await asyncio.wait_for(task, 2)
    return [m for m in socket.texts if m["request_id"] == message["request_id"]]


def _assert_logged_without_message(caplog):
    logged = caplog.text
    assert MARKER not in logged
    assert "ValueError" in logged and "Traceback" not in logged
    assert ".py" in logged  # the stack frames are kept for debugging


async def test_an_unexpected_error_in_a_turn_logs_type_and_stack_but_not_the_message(caplog):
    journey = await build_journey(model=ScriptedModel(_script()))

    def failing_persist(auth, question):
        raise ValueError(f"validation failed for input {MARKER}")

    journey.pending.persist_pending = failing_persist
    message = envelope(
        "turn.submit",
        {"utterance": "How does this line show constant resistance?", "input_mode": "keyboard", "transcript_status": "final", "expected_session_version": 10},
    )
    with caplog.at_level(logging.ERROR, logger="netra_api.transport.websocket.dispatcher"):
        frames = await _exchange(journey, message)
    assert [m["payload"]["code"] for m in frames if m["type"] == "error"] == ["INTERNAL_ERROR"]
    _assert_logged_without_message(caplog)


async def test_an_unexpected_error_while_dispatching_logs_type_and_stack_but_not_the_message(caplog, monkeypatch):
    journey = await build_journey()

    async def failing_move(*args, **kwargs):
        raise ValueError(f"reading block text {MARKER}")

    monkeypatch.setattr(journey.services.navigator, "_move", failing_move)
    message = envelope("navigation.command", {"command": "next", "expected_session_version": 10})
    with caplog.at_level(logging.ERROR, logger="netra_api.transport.websocket.dispatcher"):
        frames = await _exchange(journey, message)
    assert [m["payload"]["code"] for m in frames if m["type"] == "error"] == ["INTERNAL_ERROR"]
    _assert_logged_without_message(caplog)
