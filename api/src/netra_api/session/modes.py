"""Enumerations for session connection state and interaction (learning) mode.

Connection state and interaction mode are tracked independently: a
client can be reconnecting mid-quiz, so neither value may be inferred
from the other (see task scope: "connection state separately from
learning mode").
"""

from __future__ import annotations

from enum import Enum


class ConnectionState(str, Enum):
    """Transport-level state of the client connection for this session.

    Unchanged by the 2026-09-12 InteractionMode canonicalization: this
    vocabulary was already correct and already separate from learning
    mode. A disconnected student may still read cached content, so this
    must never be folded into InteractionMode.
    """

    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    DISCONNECTED = "disconnected"


class InteractionMode(str, Enum):
    """Current learning-flow mode the session is in.

    Canonical vocabulary approved 2026-09-12 (see docs/architecture/
    message-flow.md and data-ownership.md). Deliberately excludes:

    - "listening"/"answering"/"waiting_for_answer" (Engineering Plan
      §7.1's illustrative list): whether audio is currently rendering is
      client-local PlaybackStatus, not learning-flow state, and "awaiting
      an answer" is fully derivable from a non-null pending_question
      within TUTOR_LESSON/QUIZ rather than needing its own value that
      could drift out of sync with pending_question.
    - "navigation" (this enum's own prior value): deterministic commands
      complete synchronously; there is no persisted "currently
      navigating" interval. "A navigation command temporarily leaves the
      lesson while preserving the outstanding question" (Plan §7.1) is
      already satisfied by pending_question surviving independently of
      mode — mode can stay READING while a question waits.
    - "disconnected": belongs to ConnectionState, not here (see below).
    """

    IDLE = "idle"
    READING = "reading"
    TUTOR_LESSON = "tutor_lesson"
    QUIZ = "quiz"


class NavigationUnit(str, Enum):
    """Mirrors NavigationCommand.navigation_unit in
    shared/contracts/protocol/v1/client_to_server.schema.json.
    """

    SENTENCE = "sentence"
    BLOCK = "block"
    HEADING = "heading"
    FIGURE = "figure"
    EQUATION = "equation"
