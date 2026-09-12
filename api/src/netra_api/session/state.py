"""Session state models.

A session is the single source of truth for where a student is in a
reading/tutoring flow. PostgreSQL is the authoritative store for this
state (see CLAUDE.md "Data authority" and "Session rules"); the models
here define its shape so the Coordinator, Tutor, and transport layer
share one representation instead of passing around loose dicts.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field

from netra_api.session.modes import ConnectionState, InteractionMode


class AccountContext(BaseModel):
    """Minimal account reference. netra_api.identity owns the full account record."""

    account_id: UUID


class ReadingPosition(BaseModel):
    """Current place in the active source version.

    source_version_id/current_block_id/current_sentence_id are opaque
    identifiers owned by the Content/Ingestion service; the session only
    stores references, never reading-block content.
    """

    source_version_id: Optional[str] = None
    current_block_id: Optional[str] = None
    current_sentence_id: Optional[str] = None


class PlaybackAcknowledgement(BaseModel):
    """Last playback position the client confirmed, per the shared PlaybackAck payload."""

    generation_id: Optional[str] = None
    segment_id: Optional[str] = None
    sentence_id: Optional[str] = None
    status: Optional[str] = None
    """One of "started" | "progress" | "completed", per client_to_server.schema.json."""
    played_ms: Optional[int] = Field(default=None, ge=0)


class ActiveLessonRef(BaseModel):
    """Reference to the Tutor lesson currently in progress, if any."""

    lesson_id: UUID
    handoff_id: Optional[UUID] = None
    started_at: Optional[datetime] = None


class PendingQuestionRef(BaseModel):
    """Reference to a quiz question already persisted and awaiting a student answer.

    The answer key never lives here; this only tracks that a question is
    outstanding so the client can be routed back to it (CLAUDE.md:
    "Persist a pending question before delivering it to the student").
    """

    question_id: str
    question_version: int = Field(ge=1)
    hints_used: int = Field(default=0, ge=0)


class ResultSetRef(BaseModel):
    """Reference to the most recent retrieval/search result set, for follow-up turns."""

    result_set_id: str
    created_at: datetime


class SessionState(BaseModel):
    """Full state tracked for one session. PostgreSQL is the authoritative store."""

    session_id: UUID
    account: AccountContext
    connection_state: ConnectionState
    interaction_mode: InteractionMode
    reading_position: ReadingPosition = Field(default_factory=ReadingPosition)
    last_playback_ack: Optional[PlaybackAcknowledgement] = None
    active_lesson: Optional[ActiveLessonRef] = None
    pending_question: Optional[PendingQuestionRef] = None
    last_result_set: Optional[ResultSetRef] = None
    session_version: int = Field(default=0, ge=0)
    updated_at: datetime

    def with_incremented_version(self) -> "SessionState":
        """Return a copy with session_version incremented by one.

        Callers build the next state this way before persisting; the
        repository layer performs the actual optimistic-version check
        against the previously stored version (see
        netra_api.platform.idempotency.check_expected_version).
        """

        return self.model_copy(update={"session_version": self.session_version + 1})
