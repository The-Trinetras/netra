"""Authenticated WebSocket path: strict parsing, identity checks, replay, versions, navigation.

All collaborators are LABELLED FIXTURES (ohm_fixture.py) and repositories are
in-memory: these tests prove M1 transport/session policy, not persistence.
"""

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from netra_api.session.modes import InteractionMode
from netra_api.transport.websocket.endpoint import CLOSE_POLICY_VIOLATION, CLOSE_UNSUPPORTED_DATA, serve

from ohm_fixture import (
    DEVICE,
    OTHER_SESSION,
    SESSION,
    ScriptedModel,
    FakeSocket,
    build_journey,
    envelope,
    fid,
    sid,
    wait_for,
)


async def _open(journey, token="fixture-token-asha-0123456789abcdef"):
    socket = FakeSocket(token)
    task = asyncio.ensure_future(serve(socket, journey.services, journey.composition.verifier))
    await wait_for(lambda: socket.accepted or socket.closed_code is not None)
    return socket, task


async def _send(socket, message, message_type=None, count=1):
    socket.push(message)
    request_id = message["request_id"]
    await wait_for(lambda: len([m for m in socket.texts if m["request_id"] == request_id and (message_type is None or m["type"] == message_type)]) >= count)
    return [m for m in socket.texts if m["request_id"] == request_id]


async def _close(socket, task):
    socket.disconnect()
    await asyncio.wait_for(task, 2)


def _nav(command, version, request_id=None, unit=None):
    payload = {"command": command, "expected_session_version": version}
    if unit:
        payload["navigation_unit"] = unit
    return envelope("navigation.command", payload, request_id=request_id)


async def test_missing_credential_is_refused_before_accept():
    journey = await build_journey()
    socket = FakeSocket(token=None)
    await serve(socket, journey.services, journey.composition.verifier)
    assert socket.accepted is False
    assert socket.closed_code == CLOSE_POLICY_VIOLATION


async def test_unknown_credential_is_refused_before_accept():
    journey = await build_journey()
    socket = FakeSocket(token="not-an-issued-token")
    await serve(socket, journey.services, journey.composition.verifier)
    assert socket.accepted is False


async def test_client_binary_frame_is_refused_not_reinterpreted():
    journey = await build_journey()
    socket, task = await _open(journey)
    socket.inbox.put_nowait({"type": "websocket.receive", "bytes": b"\x00\x00\x00\x02{}"})
    await asyncio.wait_for(task, 2)
    assert socket.closed_code == CLOSE_UNSUPPORTED_DATA


async def test_unknown_field_is_rejected_and_changes_nothing():
    journey = await build_journey()
    socket, task = await _open(journey)
    message = _nav("next", 10)
    message["payload"]["account_id"] = str(uuid4())
    responses = await _send(socket, message, "error")
    assert responses[0]["payload"]["code"] == "INVALID_REQUEST"
    assert (await journey.sessions.get(SESSION)).session_version == 10
    await _close(socket, task)


async def test_interim_transcript_cannot_submit_a_turn():
    model = ScriptedModel([])
    journey = await build_journey(model=model)
    socket, task = await _open(journey)
    message = envelope("turn.submit", {"utterance": "next", "input_mode": "voice", "transcript_status": "interim", "expected_session_version": 10})
    responses = await _send(socket, message, "error")
    assert responses[0]["payload"]["code"] == "INVALID_REQUEST"
    assert model.calls == 0
    assert (await journey.sessions.get(SESSION)).session_version == 10
    await _close(socket, task)


async def test_unsupported_protocol_version_is_typed():
    journey = await build_journey()
    socket, task = await _open(journey)
    message = _nav("next", 10)
    message["protocol_version"] = "2.0"
    responses = await _send(socket, message, "error")
    assert responses[0]["payload"]["code"] == "UNSUPPORTED_PROTOCOL_VERSION"
    await _close(socket, task)


async def test_session_id_of_another_account_is_denied():
    journey = await build_journey()
    socket, task = await _open(journey)
    message = envelope("navigation.command", {"command": "next", "expected_session_version": 10}, session_id=OTHER_SESSION)
    responses = await _send(socket, message, "error")
    assert responses[0]["payload"]["code"] == "AUTHORIZATION_DENIED"
    assert (await journey.sessions.get(OTHER_SESSION)).session_version == 10
    await _close(socket, task)


async def test_device_revoked_after_accept_loses_access():
    journey = await build_journey()
    socket, task = await _open(journey)
    device = journey.identity.devices[(fid("account-asha"), DEVICE)]
    journey.identity.add_device(device.model_copy(update={"revoked_at": datetime.now(timezone.utc)}))
    responses = await _send(socket, _nav("next", 10), "error")
    assert responses[0]["payload"]["code"] == "AUTHORIZATION_DENIED"
    await _close(socket, task)


async def test_deterministic_next_moves_once_and_calls_no_model():
    model = ScriptedModel([])
    journey = await build_journey(model=model)
    socket, task = await _open(journey)
    responses = await _send(socket, _nav("next", 10), "response.segment")

    snapshot = next(m for m in responses if m["type"] == "session.snapshot")["payload"]
    segment = next(m for m in responses if m["type"] == "response.segment")["payload"]
    assert snapshot["session_version"] == 11
    assert snapshot["current_block_id"] == str(fid("b13"))
    assert segment["sentence_id"] == str(sid("b13", "s1"))
    assert "kind" not in segment  # source reading: kind omitted (proposed convention)
    assert model.calls == 0
    await _close(socket, task)


async def test_typed_command_utterance_routes_deterministically():
    model = ScriptedModel([])
    journey = await build_journey(model=model)
    socket, task = await _open(journey)
    message = envelope("turn.submit", {"utterance": "Where am I?", "input_mode": "voice", "transcript_status": "final", "expected_session_version": 10})
    responses = await _send(socket, message, "response.segment")
    text = next(m for m in responses if m["type"] == "response.segment")["payload"]["text"]
    assert text.startswith("Block 3 of 6, sentence 3 of 3, page 2.")
    assert model.calls == 0
    assert (await journey.sessions.get(SESSION)).session_version == 10  # orientation never moves or versions
    await _close(socket, task)


async def test_identical_retry_replays_even_after_version_moved():
    journey = await build_journey()
    socket, task = await _open(journey)
    first = uuid4()
    original = await _send(socket, _nav("next", 10, request_id=first), "response.segment")
    await _send(socket, _nav("next", 11), "response.segment")
    assert (await journey.sessions.get(SESSION)).session_version == 12

    commits = journey.sessions.commit_count
    socket.texts.clear()
    replayed = await _send(socket, _nav("next", 10, request_id=first), "response.segment")

    assert [m["payload"] for m in replayed] == [m["payload"] for m in original]
    assert journey.sessions.commit_count == commits
    assert (await journey.sessions.get(SESSION)).session_version == 12
    await _close(socket, task)


async def test_request_id_reused_for_different_command_fails_closed():
    journey = await build_journey()
    socket, task = await _open(journey)
    reused = uuid4()
    await _send(socket, _nav("next", 10, request_id=reused), "response.segment")
    socket.texts.clear()
    responses = await _send(socket, _nav("previous", 11, request_id=reused), "error")
    assert responses[0]["payload"]["code"] == "REQUEST_ID_CONFLICT"
    assert responses[0]["payload"]["retryable"] is False
    state = await journey.sessions.get(SESSION)
    assert state.session_version == 11
    assert state.reading_position.current_block_id == str(fid("b13"))
    await _close(socket, task)


async def test_simultaneous_different_mutations_one_wins_one_conflicts():
    journey = await build_journey()
    a, task_a = await _open(journey)
    b, task_b = await _open(journey)
    first, second = _nav("next", 10), _nav("previous", 10)
    a.push(first)
    b.push(second)
    await wait_for(lambda: a.of_type("session.snapshot") or a.of_type("error"))
    await wait_for(lambda: b.of_type("session.snapshot") or b.of_type("error"))

    errors = a.of_type("error") + b.of_type("error")
    assert len(errors) == 1
    assert errors[0]["payload"]["code"] == "SESSION_VERSION_CONFLICT"
    assert errors[0]["payload"]["current_session_version"] == 11
    assert (await journey.sessions.get(SESSION)).session_version == 11
    await _close(a, task_a)
    await _close(b, task_b)


async def test_simultaneous_identical_retransmissions_have_one_effect():
    journey = await build_journey()
    a, task_a = await _open(journey)
    b, task_b = await _open(journey)
    request_id = uuid4()
    a.push(_nav("next", 10, request_id=request_id))
    b.push(_nav("next", 10, request_id=request_id))
    await wait_for(lambda: a.of_type("session.snapshot") and b.of_type("session.snapshot"))

    assert not a.of_type("error") and not b.of_type("error")
    assert a.of_type("session.snapshot")[0]["payload"] == b.of_type("session.snapshot")[0]["payload"]
    assert (await journey.sessions.get(SESSION)).session_version == 11
    await _close(a, task_a)
    await _close(b, task_b)


async def test_stale_expected_version_for_new_request_conflicts():
    journey = await build_journey()
    socket, task = await _open(journey)
    responses = await _send(socket, _nav("next", 7), "error")
    assert responses[0]["payload"] == {
        "code": "SESSION_VERSION_CONFLICT",
        "message": responses[0]["payload"]["message"],
        "retryable": False,
        "current_session_version": 10,
    }
    await _close(socket, task)


async def test_block_jump_then_undo_and_back_to_reading_restore_exact_positions():
    journey = await build_journey()
    socket, task = await _open(journey)
    await _send(socket, _nav("next", 10, unit="equation"), "response.segment")
    state = await journey.sessions.get(SESSION)
    assert state.reading_position.current_block_id == str(fid("b15"))

    responses = await _send(socket, _nav("undo_jump", 11), "response.segment")
    assert next(m for m in responses if m["type"] == "response.segment")["payload"]["sentence_id"] == str(sid("b12", "s3"))
    state = await journey.sessions.get(SESSION)
    assert state.reading_position.current_sentence_id == str(sid("b12", "s3"))
    assert state.undo_jump_position.current_block_id == str(fid("b15"))
    await _close(socket, task)


async def test_return_to_question_without_pending_question_is_a_notice_not_an_invention():
    model = ScriptedModel([])
    journey = await build_journey(model=model)
    socket, task = await _open(journey)
    responses = await _send(socket, _nav("return_to_question", 10), "response.segment")
    assert next(m for m in responses if m["type"] == "response.segment")["payload"]["text"] == "No question is waiting."
    assert not [m for m in responses if m["type"] == "quiz.question"]
    assert model.calls == 0
    await _close(socket, task)


async def test_playback_ack_moves_position_only_for_completed_delivered_reading():
    journey = await build_journey()
    socket, task = await _open(journey)
    responses = await _send(socket, _nav("next", 10), "response.segment")
    segment = next(m for m in responses if m["type"] == "response.segment")["payload"]
    ack = {"generation_id": segment["generation_id"], "segment_id": segment["segment_id"], "sentence_id": segment["sentence_id"]}

    socket.push(envelope("playback.ack", {**ack, "status": "started"}))
    await asyncio.sleep(0.05)
    assert (await journey.sessions.get(SESSION)).session_version == 11

    completed = envelope("playback.ack", {**ack, "status": "completed"})
    snapshot = (await _send(socket, completed, "session.snapshot"))[0]["payload"]
    assert snapshot["session_version"] == 12
    assert snapshot["last_acknowledged_sentence_id"] == segment["sentence_id"]

    socket.push(envelope("playback.ack", {**ack, "status": "completed"}))
    await asyncio.sleep(0.05)
    assert (await journey.sessions.get(SESSION)).session_version == 12

    forged = envelope("playback.ack", {**ack, "sentence_id": str(sid("b15", "s1")), "status": "completed"})
    error = (await _send(socket, forged, "error"))[0]["payload"]
    assert error["code"] == "STALE_REQUEST"
    assert (await journey.sessions.get(SESSION)).reading_position.current_block_id == str(fid("b13"))
    await _close(socket, task)


async def test_resume_sends_canonical_snapshot_without_model_calls():
    model = ScriptedModel([])
    journey = await build_journey(model=model)
    socket, task = await _open(journey)
    responses = await _send(socket, envelope("session.resume", {"last_known_session_version": 3}), "session.snapshot")
    snapshot = responses[0]["payload"]
    assert snapshot["session_version"] == 10
    assert snapshot["current_sentence_id"] == str(sid("b12", "s3"))
    assert snapshot["interaction_mode"] == InteractionMode.READING.value
    assert model.calls == 0
    await _close(socket, task)
