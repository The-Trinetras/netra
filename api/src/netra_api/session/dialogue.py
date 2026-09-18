"""Session dialogue log: the delivered public exchange, in order.

Session service owns session summaries and covered topics (data-ownership.md).
This log keeps the student's accepted final utterances and the public text
actually delivered back, so context selection can pick relevant recent turns
without replaying provider transcripts. It is not learning history: answers,
stated reasoning, feedback and assistance are committed by the Learning service
(M4); nothing here is an assessment record.

Entries are never used as canonical position, pending-question or source-version
facts — those always come from SessionState.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

DialogueRole = Literal["student", "tutor", "coordinator"]

MAX_DIALOGUE_ENTRY_CHARS = 8000
"""Matches the protocol's utterance/segment text bound."""


class DialogueEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: UUID
    request_id: UUID
    ordinal: int = Field(ge=0)
    """Position within the request: the student utterance is 0, delivered
    segments follow. (request_id, ordinal) is unique, so a replayed commit
    cannot append the same exchange twice."""
    role: DialogueRole
    content: str = Field(max_length=MAX_DIALOGUE_ENTRY_CHARS)
    created_at: datetime


class DialogueLog(Protocol):
    async def append(self, entries: list[DialogueEntry]) -> None:
        """Append entries; entries whose (request_id, ordinal) already exist are ignored."""
        ...

    async def recent(self, session_id: UUID, limit: int) -> list[DialogueEntry]:
        """Most recent entries for session_id, oldest first, at most ``limit``."""
        ...
