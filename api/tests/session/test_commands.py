import pytest
from pydantic import ValidationError

from netra_api.session.commands import (
    DETERMINISTIC_COMMANDS,
    NavigationCommandName,
    NavigationCommandRequest,
)


def test_deterministic_commands_match_protocol_contract():
    expected = {
        "stop",
        "pause",
        "continue",
        "next",
        "previous",
        "repeat",
        "where_am_i",
        "back_to_reading",
        "undo_jump",
        "return_to_question",
    }
    assert {c.value for c in DETERMINISTIC_COMMANDS} == expected


def test_navigation_command_request_preserves_original_utterance():
    request = NavigationCommandRequest(
        command=NavigationCommandName.REPEAT,
        expected_session_version=8,
        original_utterance="say that again",
    )
    assert request.original_utterance == "say that again"
    assert request.command == NavigationCommandName.REPEAT


def test_navigation_command_request_rejects_negative_version():
    with pytest.raises(ValidationError):
        NavigationCommandRequest(command=NavigationCommandName.STOP, expected_session_version=-1)
