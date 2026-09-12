"""Enumerations for session connection state and interaction (learning) mode.

Connection state and interaction mode are tracked independently: a
client can be reconnecting mid-quiz, so neither value may be inferred
from the other (see task scope: "connection state separately from
learning mode").
"""

from __future__ import annotations

from enum import Enum


class ConnectionState(str, Enum):
    """Transport-level state of the client connection for this session."""

    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    DISCONNECTED = "disconnected"


class InteractionMode(str, Enum):
    """Current learning-flow mode the session is in."""

    READING = "reading"
    TUTOR_LESSON = "tutor_lesson"
    QUIZ = "quiz"
    NAVIGATION = "navigation"
    IDLE = "idle"


class NavigationUnit(str, Enum):
    """Mirrors NavigationCommand.navigation_unit in
    shared/contracts/protocol/v1/client_to_server.schema.json.
    """

    SENTENCE = "sentence"
    BLOCK = "block"
    HEADING = "heading"
    FIGURE = "figure"
    EQUATION = "equation"
