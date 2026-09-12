"""Deterministic navigation commands.

CLAUDE.md "Deterministic commands": stop/pause/continue/next/previous/
repeat/where_am_i/back_to_reading/undo_jump/return_to_question must be
handled deterministically whenever intent is unambiguous, and must never
invoke an LLM. The original utterance (when the command was derived from
speech) is kept separately so it stays available for reasoning elsewhere,
without ever being routed back through this deterministic path.
"""

from __future__ import annotations

import re
import unicodedata
from enum import Enum
from typing import Optional, Protocol

from pydantic import BaseModel, Field

from netra_api.session.modes import NavigationUnit
from netra_api.session.state import SessionState


class NavigationCommandName(str, Enum):
    """Mirrors NavigationCommand.command in
    shared/contracts/protocol/v1/client_to_server.schema.json.
    """

    STOP = "stop"
    PAUSE = "pause"
    CONTINUE = "continue"
    NEXT = "next"
    PREVIOUS = "previous"
    REPEAT = "repeat"
    WHERE_AM_I = "where_am_i"
    BACK_TO_READING = "back_to_reading"
    UNDO_JUMP = "undo_jump"
    RETURN_TO_QUESTION = "return_to_question"


DETERMINISTIC_COMMANDS = frozenset(NavigationCommandName)
"""Every command in this enum must be handled without invoking an LLM."""


SPOKEN_COMMAND_PHRASES: dict[str, NavigationCommandName] = {
    "stop": NavigationCommandName.STOP,
    "pause": NavigationCommandName.PAUSE,
    "continue": NavigationCommandName.CONTINUE,
    "next": NavigationCommandName.NEXT,
    "previous": NavigationCommandName.PREVIOUS,
    "repeat": NavigationCommandName.REPEAT,
    "where am i": NavigationCommandName.WHERE_AM_I,
    "back to reading": NavigationCommandName.BACK_TO_READING,
    "undo jump": NavigationCommandName.UNDO_JUMP,
    "return to question": NavigationCommandName.RETURN_TO_QUESTION,
}
"""The exact command phrases CLAUDE.md "Session and execution rules" lists.

This table is a transcription of that list, not a vocabulary of our own.
Deliberately no synonyms, no stemming and no fuzzy matching: which extra
phrasings count as a command is unspecified product policy, and guessing
one here would let an ambiguous utterance silently acquire deterministic
authority (coordinator.md: "Ambiguous utterances require contextual
resolution or clarification").

An utterance that is not exactly one of these falls through to the
Coordinator's model path, where it can be resolved with context. That is
the safe direction: a missed match costs a model decision, whereas a
wrong match executes an action the student did not ask for.
"""


_WHITESPACE = re.compile(r"\s+")
_TRAILING_PUNCTUATION = re.compile(r"[.!?,;:]+$")


def normalize_utterance(utterance: str) -> str:
    """Fold an utterance for command comparison only.

    coordinator.md: "Normalize command punctuation/whitespace without
    rewriting the original utterance." This returns a comparison key; the
    caller keeps the original text and stores it on
    NavigationCommandRequest.original_utterance.
    """

    folded = unicodedata.normalize("NFKC", utterance).strip().casefold()
    folded = _WHITESPACE.sub(" ", folded)
    return _TRAILING_PUNCTUATION.sub("", folded).strip()


def match_deterministic_command(utterance: str) -> Optional[NavigationCommandName]:
    """Return the command an utterance unambiguously is, or None.

    CLAUDE.md: "Handle unambiguous commands as application logic,
    bypassing LLM reasoning." This is that check, and it must run before
    any model decision is dispatched (see
    netra_api.coordinator.router.route_turn).

    Matching is exact against SPOKEN_COMMAND_PHRASES and against the wire
    values in shared/contracts/protocol/v1/client_to_server.schema.json,
    so a client may send either "where am i" or "where_am_i". Anything
    else returns None.

    Only an accepted final transcript may reach this function. The
    protocol enforces that structurally: turn.submit pins
    transcript_status to the constant "final", so an interim transcript
    cannot arrive as a turn at all.
    """

    normalized = normalize_utterance(utterance)
    if not normalized:
        return None

    matched = SPOKEN_COMMAND_PHRASES.get(normalized)
    if matched is not None:
        return matched

    try:
        return NavigationCommandName(normalized.replace(" ", "_"))
    except ValueError:
        return None


class NavigationCommandRequest(BaseModel):
    """A navigation.command payload, plus the original utterance for later reasoning."""

    command: NavigationCommandName
    navigation_unit: Optional[NavigationUnit] = None
    expected_session_version: int = Field(ge=0)
    original_utterance: Optional[str] = None
    """Preserved verbatim for reasoning/audit; never re-interpreted by this handler."""


class NavigationCommandResult(BaseModel):
    """Outcome of deterministically applying a navigation command."""

    command: NavigationCommandName
    session_version: int
    handled_deterministically: bool = True


class NavigationCommandHandler(Protocol):
    """Bounded, non-LLM handler for deterministic navigation commands.

    A concrete implementation lives alongside session/service.py once
    reading-position persistence exists; this interface only fixes the
    call shape.
    """

    def handle(
        self, session: SessionState, request: NavigationCommandRequest
    ) -> NavigationCommandResult:
        ...
