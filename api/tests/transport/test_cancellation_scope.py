"""Work started for a turn must not outlive that turn's cancellation.

A disconnect cancels the turn task directly (dispatcher.on_disconnect). The
Coordinator model call and the Tutor run each execute in their own task; when
the turn task is cancelled they must be cancelled too, not left running to
completion in the background, where a provider call keeps spending and a
Tutor run can persist questions or commit attempts for a turn that no longer
exists (while its retransmission runs the same turn again).

LABELLED FIXTURE RUN: scripted model, fixture Tutor, in-memory persistence.
"""

import asyncio

from netra_api.transport.websocket.endpoint import serve

from ohm_fixture import AXES, FakeSocket, ScriptedModel, build_journey, envelope, final, tools, wait_for


def _repair_script():
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


class OutcomeRecordingModel(ScriptedModel):
    def __init__(self, steps, *, delay: float) -> None:
        super().__init__(steps, delay=delay)
        self.completed = 0
        self.cancelled = 0

    async def decide(self, config, prompt, tools):
        try:
            decision = await super().decide(config, prompt, tools)
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        self.completed += 1
        return decision


def _turn():
    return envelope(
        "turn.submit",
        {"utterance": "How does this line show constant resistance?", "input_mode": "keyboard", "transcript_status": "final", "expected_session_version": 10},
    )


async def _open(journey):
    socket = FakeSocket()
    task = asyncio.ensure_future(serve(socket, journey.services, journey.composition.verifier))
    await wait_for(lambda: socket.accepted)
    return socket, task


async def test_disconnect_during_a_model_decision_cancels_the_provider_call():
    model = OutcomeRecordingModel(_repair_script(), delay=0.3)
    journey = await build_journey(model=model)
    socket, task = await _open(journey)
    socket.push(_turn())
    await wait_for(lambda: model.calls == 1)
    socket.disconnect()
    await asyncio.wait_for(task, 2)

    await asyncio.sleep(0.45)  # longer than the call would take if it were left running
    assert model.cancelled == 1
    assert model.completed == 0
    assert len(model.steps) == 2  # the abandoned call consumed nothing


async def test_disconnect_during_the_tutor_run_cancels_it_before_it_persists_anything():
    journey = await build_journey(model=ScriptedModel(_repair_script()), tutor_kwargs={"delay": 0.3})
    socket, task = await _open(journey)
    socket.push(_turn())
    await wait_for(lambda: bool(journey.tutor.handoffs))
    socket.disconnect()
    await asyncio.wait_for(task, 2)

    await asyncio.sleep(0.45)
    assert journey.pending.questions == {}  # the cancelled run did not persist its question
