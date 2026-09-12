"""Deterministic command matching must never let a model decide a command.

CLAUDE.md: "Handle unambiguous commands as application logic, bypassing
LLM reasoning."
"""

import json
from pathlib import Path

import pytest

from netra_api.session.commands import (
    SPOKEN_COMMAND_PHRASES,
    NavigationCommandName,
    match_deterministic_command,
    normalize_utterance,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = REPO_ROOT / "shared" / "contracts" / "protocol" / "v1" / "client_to_server.schema.json"


def test_every_contract_command_has_a_spoken_phrase():
    """The phrase table must cover the whole contract vocabulary, or some
    command would only ever be reachable through the model path."""

    schema = json.loads(SCHEMA_PATH.read_text())
    schema_commands = set(schema["$defs"]["NavigationCommand"]["properties"]["command"]["enum"])
    covered = {command.value for command in SPOKEN_COMMAND_PHRASES.values()}
    assert covered == schema_commands


@pytest.mark.parametrize(
    ("utterance", "expected"),
    [
        ("stop", NavigationCommandName.STOP),
        ("Stop.", NavigationCommandName.STOP),
        ("  NEXT  ", NavigationCommandName.NEXT),
        ("where am I?", NavigationCommandName.WHERE_AM_I),
        ("Back to reading", NavigationCommandName.BACK_TO_READING),
        ("return to question", NavigationCommandName.RETURN_TO_QUESTION),
        ("where_am_i", NavigationCommandName.WHERE_AM_I),
    ],
)
def test_unambiguous_utterances_match_without_a_model(utterance, expected):
    assert match_deterministic_command(utterance) == expected


@pytest.mark.parametrize(
    "utterance",
    [
        "next question",
        "can you stop explaining that",
        "what does previous mean",
        "repeat after me",
        "",
        "   ",
    ],
)
def test_non_commands_fall_through_to_the_model_path(utterance):
    """A near-miss must not acquire deterministic authority. The audit's
    case is an interim "next" followed by a final "next question": the
    final utterance must not navigate."""

    assert match_deterministic_command(utterance) is None


def test_normalization_does_not_rewrite_the_original_utterance():
    """coordinator.md: "Normalize command punctuation/whitespace without
    rewriting the original utterance." normalize_utterance returns a
    comparison key and leaves its input untouched."""

    original = "  Where am I?  "
    assert normalize_utterance(original) == "where am i"
    assert original == "  Where am I?  "
