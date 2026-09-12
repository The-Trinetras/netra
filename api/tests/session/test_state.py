from datetime import datetime, timezone
from uuid import uuid4

from netra_api.session.modes import ConnectionState, InteractionMode
from netra_api.session.state import AccountContext, SessionState


def _make_session(version: int = 0) -> SessionState:
    return SessionState(
        session_id=uuid4(),
        account=AccountContext(account_id=uuid4()),
        connection_state=ConnectionState.CONNECTED,
        interaction_mode=InteractionMode.READING,
        session_version=version,
        updated_at=datetime.now(timezone.utc),
    )


def test_session_version_starts_at_zero_by_default():
    session = _make_session()
    assert session.session_version == 0


def test_with_incremented_version_does_not_mutate_original():
    session = _make_session(version=3)
    next_session = session.with_incremented_version()
    assert session.session_version == 3
    assert next_session.session_version == 4


def test_connection_state_and_interaction_mode_are_independent():
    session = _make_session()
    reconnecting = session.model_copy(update={"connection_state": ConnectionState.RECONNECTING})
    assert reconnecting.connection_state == ConnectionState.RECONNECTING
    assert reconnecting.interaction_mode == InteractionMode.READING
