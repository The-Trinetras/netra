import json
from pathlib import Path

from netra_api.session.commands import NavigationCommandName, NavigationCommandRequest

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_PATH = REPO_ROOT / "shared" / "contracts" / "examples" / "client" / "navigation_command.json"
SCHEMA_PATH = REPO_ROOT / "shared" / "contracts" / "protocol" / "v1" / "client_to_server.schema.json"


def test_navigation_command_example_matches_our_model():
    message = json.loads(EXAMPLE_PATH.read_text())
    assert message["type"] == "navigation.command"
    request = NavigationCommandRequest.model_validate(message["payload"])
    assert request.command.value == "repeat"
    assert request.navigation_unit is not None
    assert request.navigation_unit.value == "sentence"
    assert request.expected_session_version == 8


def test_command_enum_matches_schema_enum():
    schema = json.loads(SCHEMA_PATH.read_text())
    schema_commands = set(schema["$defs"]["NavigationCommand"]["properties"]["command"]["enum"])
    assert {c.value for c in NavigationCommandName} == schema_commands
