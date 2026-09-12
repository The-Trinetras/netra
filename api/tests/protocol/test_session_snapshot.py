"""session.snapshot: reference-only, every key present with explicit nulls."""

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from netra_api.session.modes import ConnectionState, InteractionMode
from netra_api.session.state import (
    ActiveLessonRef,
    AccountContext,
    PendingQuestionRef,
    ResultSetRef,
    SessionState,
)
from netra_api.transport.websocket.serializer import ErrorPayload, QuizQuestionPayload
from netra_api.transport.websocket.snapshots import build_session_snapshot

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLES = REPO_ROOT / "shared" / "contracts" / "examples" / "server"


def _example(name):
    return json.loads((EXAMPLES / name).read_text())


def _fresh_state(**overrides) -> SessionState:
    defaults = dict(
        session_id=uuid4(),
        account=AccountContext(account_id=uuid4()),
        connection_state=ConnectionState.CONNECTED,
        interaction_mode=InteractionMode.IDLE,
        updated_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return SessionState(**defaults)


def test_committed_session_snapshot_example_parses():
    payload = _example("session_snapshot.json")["payload"]
    from netra_api.transport.websocket.serializer import SessionSnapshotPayload

    snapshot = SessionSnapshotPayload.model_validate(payload)
    assert snapshot.interaction_mode == "quiz"
    assert snapshot.pending_question is not None
    assert snapshot.pending_question.hints_used == 1


def test_committed_quiz_question_example_parses():
    payload = _example("quiz_question.json")["payload"]
    question = QuizQuestionPayload.model_validate(payload)
    assert question.kind == "multiple_choice"
    assert len(question.options) == 2


def test_committed_error_example_parses():
    payload = _example("error.json")["payload"]
    error = ErrorPayload.model_validate(payload)
    assert error.code == "SESSION_VERSION_CONFLICT"
    assert error.current_session_version == 18


def test_fresh_idle_session_has_explicit_nulls_not_missing_keys():
    """A brand new session (never opened a source) must still produce a
    complete snapshot: every key present, with null where nothing exists
    yet — never an omitted key."""

    state = _fresh_state()
    snapshot = build_session_snapshot(state)

    assert snapshot.active_source_version_id is None
    assert snapshot.current_block_id is None
    assert snapshot.active_lesson is None
    assert snapshot.pending_question is None
    assert snapshot.last_result_set is None
    # model_dump always includes every field, confirming no key is ever omitted.
    dumped = snapshot.model_dump()
    assert set(dumped) == {
        "session_version",
        "interaction_mode",
        "active_source_version_id",
        "current_block_id",
        "current_sentence_id",
        "last_acknowledged_sentence_id",
        "active_lesson",
        "pending_question",
        "last_result_set",
    }


def test_snapshot_reflects_pending_question_and_result_set_references_only():
    """The snapshot must carry references, never the full question/result
    content — that would turn it into a history dump."""

    result_set_id = uuid4()
    state = _fresh_state(
        interaction_mode=InteractionMode.QUIZ,
        pending_question=PendingQuestionRef(question_id="q-1", question_version=2, hints_used=1),
        last_result_set=ResultSetRef(result_set_id=result_set_id, created_at=datetime.now(timezone.utc)),
    )
    snapshot = build_session_snapshot(state)

    assert snapshot.pending_question.question_id == "q-1"
    assert snapshot.pending_question.hints_used == 1
    assert snapshot.last_result_set.result_set_id == result_set_id
    # No prompt/options/items fields exist on these reference types at all.
    assert not hasattr(snapshot.pending_question, "prompt")
    assert not hasattr(snapshot.last_result_set, "items")


def test_snapshot_reflects_active_lesson_reference():
    lesson_id = uuid4()
    state = _fresh_state(
        interaction_mode=InteractionMode.TUTOR_LESSON,
        active_lesson=ActiveLessonRef(lesson_id=lesson_id),
    )
    snapshot = build_session_snapshot(state)

    assert snapshot.active_lesson.lesson_id == lesson_id
