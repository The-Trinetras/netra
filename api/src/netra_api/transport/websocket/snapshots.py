"""Builds the session.snapshot wire payload from canonical SessionState.

Kept separate from serializer.py (pure schema mirroring) because this
module depends on netra_api.session, and serializer.py must stay free of
that dependency to avoid a cycle (session.commands/session.modes are
already imported by serializer.py for the *client* payload types).
"""

from __future__ import annotations

from netra_api.session.state import SessionState
from netra_api.transport.websocket.serializer import (
    ActiveLessonRefPayload,
    PendingQuestionRefPayload,
    ResultSetRefPayload,
    SessionSnapshotPayload,
)


def build_session_snapshot(state: SessionState) -> SessionSnapshotPayload:
    """Project canonical SessionState onto the reference-only snapshot shape.

    Deliberately omits connection_state, per-generation playback detail
    and any return-navigation position — see the SessionSnapshot $def's
    $comment in shared/contracts/protocol/v1/server_to_client.schema.json
    for why each was excluded. Callers that need to also restore the full
    pending question content send a fresh quiz.question message right
    after this snapshot (message-flow.md flow 11); this function does not
    do that itself, since it only knows about session state, not the
    question repository.
    """

    return SessionSnapshotPayload(
        session_version=state.session_version,
        interaction_mode=state.interaction_mode.value,  # type: ignore[arg-type]
        active_source_version_id=state.reading_position.source_version_id,
        current_block_id=state.reading_position.current_block_id,
        current_sentence_id=state.reading_position.current_sentence_id,
        last_acknowledged_sentence_id=(
            state.last_playback_ack.sentence_id if state.last_playback_ack is not None else None
        ),
        active_lesson=(
            ActiveLessonRefPayload(lesson_id=state.active_lesson.lesson_id)
            if state.active_lesson is not None
            else None
        ),
        pending_question=(
            PendingQuestionRefPayload(
                question_id=state.pending_question.question_id,
                question_version=state.pending_question.question_version,
                hints_used=state.pending_question.hints_used,
            )
            if state.pending_question is not None
            else None
        ),
        last_result_set=(
            ResultSetRefPayload(
                result_set_id=state.last_result_set.result_set_id,
                created_at=state.last_result_set.created_at,
            )
            if state.last_result_set is not None
            else None
        ),
    )
