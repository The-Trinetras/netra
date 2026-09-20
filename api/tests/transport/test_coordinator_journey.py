"""Ohm's-law journey through the real M1 transport, session service and Coordinator loop.

LABELLED FIXTURE RUN: the Coordinator model is scripted, M2/M3/M4 services are
fixture doubles and persistence is in-memory. This proves the harness —
evidence-gap repair changing the next action, validation, shared budgets,
fencing and recovery — not live model judgement or source fidelity.
"""

import asyncio
import json
from uuid import uuid4

from netra_api.coordinator.limits import MAX_MODEL_DECISIONS_PER_TURN
from netra_api.session.modes import InteractionMode
from netra_api.transport.websocket.endpoint import serve

from ohm_fixture import (
    AXES,
    SESSION,
    FakeSocket,
    ScriptedModel,
    build_journey,
    envelope,
    fid,
    final,
    sid,
    tools,
    wait_for,
)

QUESTION = "How does this line show constant resistance, and where does the table show it?"


async def _open(journey):
    socket = FakeSocket()
    task = asyncio.ensure_future(serve(socket, journey.services, journey.composition.verifier))
    await wait_for(lambda: socket.accepted)
    return socket, task


async def _close(socket, task):
    socket.disconnect()
    await asyncio.wait_for(task, 2)


def _turn(utterance=QUESTION, version=10, request_id=None):
    return envelope(
        "turn.submit",
        {"utterance": utterance, "input_mode": "voice", "transcript_status": "final", "expected_session_version": version},
        request_id=request_id,
    )


async def _submit(socket, message, until="session.snapshot", timeout=3.0):
    socket.push(message)
    request_id = message["request_id"]
    await wait_for(lambda: any(m["request_id"] == request_id and m["type"] in (until, "error") for m in socket.texts), timeout)
    return [m for m in socket.texts if m["request_id"] == request_id]


def _repair_script():
    return [
        tools(("search_sources", {"query": "this line constant resistance"}), requirements=AXES),
        tools(
            ("describe_figure", {"figure_index": 1}),
            assessments=[
                {"requirement_id": "x-axis", "status": "missing", "evidence_id": "ev-passage-b12", "gap": "axes not established from text"},
                {"requirement_id": "y-axis", "status": "missing", "evidence_id": "ev-passage-b12", "gap": "axes not established from text"},
            ],
        ),
        final(
            {
                "action": "delegate_to_tutor",
                "assessments": [
                    {"requirement_id": "x-axis", "status": "supported", "evidence_id": "ev-fig02", "observation_label": "x-axis: current (A)"},
                    {"requirement_id": "y-axis", "status": "supported", "evidence_id": "ev-fig02", "observation_label": "y-axis: voltage (V)"},
                ],
                "tutor": {"mode": "explain", "learning_goal": "Connect the graph and table to constant resistance.", "evidence_ids": ["ev-fig02", "ev-passage-b12"]},
            }
        ),
    ]


async def test_missing_axes_gap_changes_the_next_action_and_is_validated_before_teaching():
    model = ScriptedModel(_repair_script())
    journey = await build_journey(model=model)
    socket, task = await _open(journey)

    responses = await _submit(socket, _turn(), until="quiz.question")
    kinds = [m["type"] for m in responses]
    assert kinds[0] == "session.snapshot" and kinds[-1] == "quiz.question"
    segments = [m["payload"] for m in responses if m["type"] == "response.segment"]
    assert segments[0]["kind"] == "explanation" and "2/1 = 4/2 = 6/3 = 2 ohms" in segments[0]["text"]
    question = responses[-1]["payload"]
    assert question["question_id"] == "q01" and question["hints_used"] == 0
    assert "answer_key" not in json.dumps(responses) and "8 volts" not in json.dumps(responses)

    trace = journey.trace.kinds()
    # The decisive trace: insufficient result -> recorded gap -> changed action -> validated evidence.
    first_result = trace.index("tool_result")
    gap = trace.index("evidence_gap")
    changed = trace.index("action_changed")
    validated = trace.index("evidence_validated")
    assert first_result < gap < changed < validated < trace.index("handoff_sent")
    gap_detail = journey.trace.of_kind("evidence_gap")[0].detail
    assert gap_detail["requirement"] == "x-axis" and gap_detail["gap"] == "reported_missing"
    assert journey.trace.of_kind("action_changed")[0].detail["new_tools"] == ["describe_figure"]

    # Shared budget: 3 Coordinator decisions + 1 Tutor decision on the SAME
    # instance. Four is the count this script produces, not the ceiling: the
    # two were equal only while the ceiling happened to be 4 (D-BUDGET-2).
    assert model.calls == 3
    tutor_budget = journey.tutor.budgets[0]
    assert tutor_budget.model_decisions_used == 4
    assert tutor_budget.max_model_decisions == MAX_MODEL_DECISIONS_PER_TURN
    assert tutor_budget.tool_calls_used == 2
    handoff = journey.tutor.handoffs[0]
    assert handoff.deadline_at == tutor_budget.deadline_at
    assert [ref.evidence_id for ref in handoff.evidence_refs] == ["ev-fig02", "ev-passage-b12"]
    assert handoff.assessment_summaries == []

    state = await journey.sessions.get(SESSION)
    assert state.interaction_mode == InteractionMode.TUTOR_LESSON
    assert state.pending_question.question_id == "q01"
    assert state.reading_return_position.current_sentence_id == str(sid("b12", "s3"))
    await _close(socket, task)


async def test_reconnect_restores_same_pending_question_then_exact_reading_position():
    model = ScriptedModel(_repair_script())
    journey = await build_journey(model=model)
    socket, task = await _open(journey)
    first = await _submit(socket, _turn(), until="quiz.question")
    original_question = first[-1]["payload"]
    await _close(socket, task)

    calls_before = model.calls
    socket, task = await _open(journey)
    resumed = await _submit(socket, envelope("session.resume", {"last_known_session_version": 10}), until="quiz.question")
    assert resumed[0]["type"] == "session.snapshot"
    assert resumed[0]["payload"]["pending_question"] == {"question_id": "q01", "question_version": 1, "hints_used": 0}
    assert resumed[-1]["payload"] == original_question
    assert model.calls == calls_before  # waiting and reconnecting consume no model calls

    version = resumed[0]["payload"]["session_version"]
    back = await _submit(socket, envelope("navigation.command", {"command": "back_to_reading", "expected_session_version": version}), until="response.segment")
    segment = next(m for m in back if m["type"] == "response.segment")["payload"]
    assert segment["sentence_id"] == str(sid("b12", "s3"))
    state = await journey.sessions.get(SESSION)
    assert state.reading_position.current_sentence_id == str(sid("b12", "s3"))
    assert state.pending_question.question_id == "q01"  # the question survives leaving the lesson

    again = await _submit(socket, envelope("navigation.command", {"command": "return_to_question", "expected_session_version": state.session_version}), until="quiz.question")
    assert again[-1]["payload"] == original_question
    await _close(socket, task)


async def test_sufficient_first_evidence_answers_without_repair():
    model = ScriptedModel(
        [
            tools(("search_sources", {"query": "table values"}), requirements=[{"requirement_id": "rows", "description": "table rows"}]),
            final(
                {
                    "action": "answer",
                    "assessments": [{"requirement_id": "rows", "status": "supported", "evidence_id": "ev-table-tbl01"}],
                    "text": "Table 1 lists (1 A, 2 V), (2 A, 4 V) and (3 A, 6 V).",
                    "cited_evidence_ids": ["ev-table-tbl01"],
                }
            ),
        ]
    )
    journey = await build_journey(model=model)
    socket, task = await _open(journey)
    responses = await _submit(socket, _turn("What does the table show?"), until="response.segment")
    segment = next(m for m in responses if m["type"] == "response.segment")["payload"]
    assert segment["evidence_ids"] == ["ev-table-tbl01"]
    assert model.calls == 2
    assert "evidence_gap" not in journey.trace.kinds() and "action_changed" not in journey.trace.kinds()
    assert journey.figures.calls == 0
    await _close(socket, task)


async def test_unreadable_axes_stay_a_stated_gap_and_identical_repeat_is_blocked():
    model = ScriptedModel(
        [
            tools(("search_sources", {"query": "this line"}), requirements=AXES[:1]),
            tools(("describe_figure", {"figure_index": 1}), assessments=[{"requirement_id": "x-axis", "status": "missing", "evidence_id": "ev-passage-b12"}]),
            final(
                {
                    "action": "answer",
                    "assessments": [{"requirement_id": "x-axis", "status": "supported", "evidence_id": "ev-fig02", "observation_label": "region 1"}],
                    "text": "The x-axis is current in amperes.",
                    "cited_evidence_ids": ["ev-fig02"],
                }
            ),
            tools(("describe_figure", {"figure_index": 1})),
        ]
    )
    journey = await build_journey(model=model, figures_readable=False)
    socket, task = await _open(journey)
    responses = await _submit(socket, _turn(), until="response.segment")
    text = next(m for m in responses if m["type"] == "response.segment")["payload"]["text"]

    assert "unreadable in the source" in text and "won't guess" in text
    assert "current in amperes" not in text
    gaps = [e.detail for e in journey.trace.of_kind("evidence_gap")]
    assert {"requirement": "x-axis", "status": "unreadable", "gap": "unreadable_in_source", "evidence_id": "ev-fig02"} in gaps
    assert journey.trace.of_kind("repetition_blocked")
    assert journey.figures.calls == 1
    assert journey.tutor.handoffs == []
    await _close(socket, task)


async def test_injected_source_instructions_and_unauthorized_evidence_grant_nothing():
    model = ScriptedModel(
        [
            tools(("search_sources", {"query": "inject"}), requirements=[{"requirement_id": "notes", "description": "notes"}]),
            tools(("search_sources", {"query": "inject", "account_id": "someone-else"})),
            final({"action": "answer", "assessments": [{"requirement_id": "notes", "status": "supported", "evidence_id": "ev-other-student"}], "text": "Here are the notes.", "cited_evidence_ids": ["ev-other-student"]}),
            final({"action": "state_gap", "text": "I can only use your own material."}),
        ]
    )
    journey = await build_journey(model=model, retrieval_hits={"inject": ["ev-injected", "ev-other-student"]})
    socket, task = await _open(journey)
    responses = await _submit(socket, _turn("Summarize the notes."), until="response.segment")

    outbound = json.dumps(responses)
    assert "Another student's private notes" not in outbound
    assert all("Another student's private notes" not in prompt for prompt in model.prompts)
    assert "IGNORE THE TASK" in model.prompts[1]
    assert "<untrusted_evidence id=\"ev-injected\"" in model.prompts[1]
    assert next(m for m in responses if m["type"] == "response.segment")["payload"]["text"] == "I can only use your own material."
    rejected = [e.detail for e in journey.trace.of_kind("tool_rejected")]
    assert {"tool": "search_sources", "reason": "invalid_arguments"} in rejected
    assert journey.trace.of_kind("evidence_rejected")[0].detail["count"] == 1
    assert any("cited_evidence_not_validated" in str(e.detail) for e in journey.trace.of_kind("model_output_rejected"))
    await _close(socket, task)


async def test_invalid_model_output_exhausts_the_shared_budget_with_a_bounded_reply():
    model = ScriptedModel([final({"action": "answer"})] * 10)
    journey = await build_journey(model=model)
    socket, task = await _open(journey)
    responses = await _submit(socket, _turn(), until="response.segment")
    text = next(m for m in responses if m["type"] == "response.segment")["payload"]["text"]
    assert text.startswith("I stopped before finishing this answer.")
    assert model.calls == MAX_MODEL_DECISIONS_PER_TURN
    assert journey.trace.of_kind("budget_exhausted")[0].detail["limit"] == "model_decisions"
    await _close(socket, task)


async def test_budget_exhausted_inside_the_tutor_is_reported_as_the_limit_not_an_outage():
    # Rejected outputs until the repair script's last decision delegates on
    # the very last decision the budget allows; the Tutor's own decision would
    # then be one past it, on the shared instance. Sized from the constant so
    # the limit is still reached after D-BUDGET-2 raised it.
    script = _repair_script()
    padding = [final({"action": "answer"})] * (MAX_MODEL_DECISIONS_PER_TURN - len(script))
    model = ScriptedModel([*padding, *script])
    journey = await build_journey(model=model)
    socket, task = await _open(journey)
    responses = await _submit(socket, _turn(), until="response.segment")
    text = next(m for m in responses if m["type"] == "response.segment")["payload"]["text"]
    assert text.startswith("I stopped before finishing this answer.")
    assert "Supported by the material" in text and "ev-fig02" in text
    assert "service is unavailable" not in text
    assert model.calls == MAX_MODEL_DECISIONS_PER_TURN
    assert journey.trace.of_kind("budget_exhausted")[0].detail["limit"] == "model_decisions"
    assert journey.tutor.budgets[0].model_decisions_used == MAX_MODEL_DECISIONS_PER_TURN  # not enlarged
    assert journey.pending.questions == {}
    await _close(socket, task)


async def test_stop_during_model_decision_cancels_silently_and_retry_cannot_revive_it():
    model = ScriptedModel(_repair_script(), delay=0.3)
    journey = await build_journey(model=model)
    socket, task = await _open(journey)
    turn_id = uuid4()
    socket.push(_turn(request_id=turn_id))
    await wait_for(lambda: model.calls == 1)
    socket.push(envelope("response.cancel", {"cancel_request_id": str(turn_id), "reason": "user_stop"}))
    await wait_for(lambda: any(m["request_id"] == str(turn_id) for m in socket.texts))
    await asyncio.sleep(0.4)

    turn_messages = [m for m in socket.texts if m["request_id"] == str(turn_id)]
    assert [m["type"] for m in turn_messages] == ["session.snapshot"]
    assert "turn_cancelled" in journey.trace.kinds()
    assert model.calls == 1

    socket.texts.clear()
    replay = await _submit(socket, _turn(request_id=turn_id))
    assert [m["type"] for m in replay] == ["session.snapshot"]
    assert model.calls == 1
    await _close(socket, task)


async def test_disconnect_mid_turn_retransmission_keeps_spent_budget():
    model = ScriptedModel([final({"action": "answer"})] * 10, delay=0.2)
    journey = await build_journey(model=model)
    socket, task = await _open(journey)
    turn_id = uuid4()
    socket.push(_turn(request_id=turn_id))
    await wait_for(lambda: model.calls == 1)
    await _close(socket, task)
    entry = journey.services.turns.get(fid("account-asha"), turn_id)
    assert entry is not None and entry.budget.model_decisions_used == 1
    assert not [m for m in socket.texts if m["request_id"] == str(turn_id)]  # nothing recorded or delivered

    socket, task = await _open(journey)
    responses = await _submit(socket, _turn(request_id=turn_id), until="response.segment", timeout=5)
    assert "I stopped before finishing" in json.dumps(responses)
    # 1 decision before the drop + 3 after = the one shared limit, not 1 + 4.
    assert model.calls == MAX_MODEL_DECISIONS_PER_TURN
    await _close(socket, task)


async def test_answer_key_leak_from_tutor_is_rejected():
    model = ScriptedModel(_repair_script())
    journey = await build_journey(model=model, tutor_kwargs={"leak_answer": True})
    socket, task = await _open(journey)
    responses = await _submit(socket, _turn(), until="response.segment")
    assert "8 volts" not in json.dumps(responses)
    assert journey.trace.of_kind("handoff_rejected")[0].detail["check"] == "private_answer_in_public_segment"
    state = await journey.sessions.get(SESSION)
    assert state.pending_question is None
    await _close(socket, task)
