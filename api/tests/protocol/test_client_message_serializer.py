"""The Python wire models must match shared/contracts, field for field."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from netra_api.platform.errors import UnsupportedProtocolVersionError
from netra_api.transport.websocket.serializer import (
    NavigationCommandPayload,
    TurnSubmitPayload,
    parse_client_message,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLES = REPO_ROOT / "shared" / "contracts" / "examples" / "client"


def _example(name):
    return json.loads((EXAMPLES / name).read_text())


def test_committed_turn_submit_example_parses():
    envelope, payload = parse_client_message(_example("turn_submit.json"))
    assert envelope.type == "turn.submit"
    assert isinstance(payload, TurnSubmitPayload)
    assert payload.input_mode == "voice"
    assert payload.expected_session_version == 7


def test_committed_navigation_command_example_parses():
    envelope, payload = parse_client_message(_example("navigation_command.json"))
    assert envelope.type == "navigation.command"
    assert isinstance(payload, NavigationCommandPayload)
    assert payload.command.value == "repeat"
    assert payload.navigation_unit.value == "sentence"


def test_unsupported_protocol_version_is_rejected_as_such():
    message = _example("turn_submit.json") | {"protocol_version": "2.0"}
    with pytest.raises(UnsupportedProtocolVersionError):
        parse_client_message(message)


def test_unknown_envelope_field_is_rejected():
    """The schemas set additionalProperties: false. Accepting an unknown
    field here would let Python and the contract drift apart silently."""

    message = _example("turn_submit.json") | {"unexpected": "value"}
    with pytest.raises(ValidationError):
        parse_client_message(message)


def test_unknown_payload_field_is_rejected():
    message = _example("turn_submit.json")
    message["payload"] = message["payload"] | {"unexpected": "value"}
    with pytest.raises(ValidationError):
        parse_client_message(message)


def test_interim_transcript_cannot_be_submitted_as_a_turn():
    """transcript_status is pinned to "final" by the contract, so an
    interim transcript cannot become a turn at all."""

    message = _example("turn_submit.json")
    message["payload"] = message["payload"] | {"transcript_status": "interim"}
    with pytest.raises(ValidationError):
        parse_client_message(message)
