"""Server-side generation registry: what was delivered, and what may still play.

message-flow.md flows 6–8: one active speaking response per session;
cancelled or superseded generations stay ineligible, including for late
arrivals and reconnect; acknowledgements must refer to eligible delivered
content; download or send completion is not playback evidence.

A *generation* is one response's output stream. This registry records, per
generation, every sentence the server actually sent (with its source-reading
locator when it is document text) and the generation's lifecycle:

    active --pause--> paused --resume--> active
    active/paused --complete--> completed      (all output sent)
    any --cancel/supersede/disconnect--> cancelled   (terminal)

Rules the registry enforces:

- Starting a new speaking generation cancels every earlier non-cancelled
  generation of the session (supersession). Text-only notices, such as a
  where-am-I orientation, do not supersede playback.
- Nothing ever leaves ``cancelled``; resume only applies to ``paused``.
- An unknown generation is treated as ineligible. That is what makes the
  bounded history below safe: evicting old records can only deny, never admit.

Scope limitation, recorded in docs/team/handoffs/M1.md: the registry is
in-process. A multi-process API deployment needs sticky session routing or a
PostgreSQL-backed registry before this fencing holds across processes.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Literal, Optional
from uuid import UUID, uuid4

GenerationStateName = Literal["active", "paused", "completed", "cancelled"]
SentenceOrigin = Literal["source_reading", "generated"]
CancelReasonName = Literal["user_stop", "new_turn", "navigation", "client_shutdown", "superseded", "disconnect"]

MAX_GENERATIONS_PER_SESSION = 32
"""Bounded history of generation records kept per session. Evicted records
become unknown, and unknown means ineligible."""

MAX_SESSIONS_RETAINED = 512
"""Bounded number of sessions whose records this process keeps. When a new
session would exceed it, the least recently started sessions with nothing
active or paused are released: STOP must always reach active audio and a
paused generation must survive to be resumed. Released records become
unknown, which again only denies. An implementation bound, not product policy."""


@dataclass(frozen=True)
class DeliveredSentence:
    segment_id: str
    sentence_id: str
    origin: SentenceOrigin
    source_version_id: Optional[str] = None
    block_id: Optional[str] = None


@dataclass
class Generation:
    generation_id: str
    session_id: UUID
    request_id: UUID
    speakable: bool
    state: GenerationStateName = "active"
    cancel_reason: Optional[CancelReasonName] = None
    sentences: dict[tuple[str, str], DeliveredSentence] = field(default_factory=dict)
    acknowledgements: set[tuple[str, str, str]] = field(default_factory=set)
    frame_sequence: int = 0
    resume_state: GenerationStateName = "active"
    """Next binary audio frame sequence for this generation; strictly
    increasing per generation as audio_frame_header.schema.json requires."""
    _deliverable: asyncio.Event = field(default_factory=asyncio.Event)

    def __post_init__(self) -> None:
        self._deliverable.set()

    @property
    def is_cancelled(self) -> bool:
        return self.state == "cancelled"

    @property
    def accepts_output(self) -> bool:
        return self.state == "active"

    async def wait_until_deliverable(self) -> bool:
        """Block while paused. Returns False once cancelled, True when active."""

        while True:
            if self.state == "cancelled":
                return False
            if self.state in ("active", "completed"):
                return self.state == "active"
            self._deliverable.clear()
            await self._deliverable.wait()


class GenerationRegistry:
    def __init__(
        self, max_generations_per_session: int = MAX_GENERATIONS_PER_SESSION, max_sessions: int = MAX_SESSIONS_RETAINED
    ) -> None:
        self._by_session: OrderedDict[UUID, OrderedDict[str, Generation]] = OrderedDict()
        self._max = max_generations_per_session
        self._max_sessions = max_sessions

    @property
    def retained_sessions(self) -> int:
        return len(self._by_session)

    def start(
        self, session_id: UUID, request_id: UUID, *, speakable: bool = True, generation_id: Optional[str] = None
    ) -> Generation:
        records = self._by_session.get(session_id)
        if records is None:
            records = self._by_session[session_id] = OrderedDict()
            self._release_idle_sessions(keep=session_id)
        else:
            self._by_session.move_to_end(session_id)
        if speakable:
            for existing in records.values():
                if existing.speakable and existing.state != "cancelled":
                    self._cancel(existing, "superseded")
        generation = Generation(
            generation_id=generation_id or str(uuid4()),
            session_id=session_id,
            request_id=request_id,
            speakable=speakable,
        )
        records[generation.generation_id] = generation
        while len(records) > self._max:
            records.popitem(last=False)
        return generation

    def _release_idle_sessions(self, keep: UUID) -> None:
        excess = len(self._by_session) - self._max_sessions
        if excess <= 0:
            return
        idle = []
        for session_id, records in self._by_session.items():  # least recently started first
            if len(idle) == excess:
                break
            if session_id != keep and not any(g.state in ("active", "paused") for g in records.values()):
                idle.append(session_id)
        for session_id in idle:
            del self._by_session[session_id]

    def next_frame_sequence(self, generation: Generation) -> int:
        sequence = generation.frame_sequence
        generation.frame_sequence += 1
        return sequence

    def get(self, session_id: UUID, generation_id: str) -> Optional[Generation]:
        return self._by_session.get(session_id, OrderedDict()).get(generation_id)

    def record_sentence(self, generation: Generation, sentence: DeliveredSentence) -> None:
        generation.sentences[(sentence.segment_id, sentence.sentence_id)] = sentence

    def complete(self, generation: Generation) -> None:
        if generation.state == "active":
            generation.state = "completed"
            generation._deliverable.set()
        elif generation.state == "paused":
            generation.resume_state = "completed"

    def cancel_generation(self, session_id: UUID, generation_id: str, reason: CancelReasonName) -> bool:
        generation = self.get(session_id, generation_id)
        if generation is None:
            return False
        self._cancel(generation, reason)
        return True

    def cancel_speaking(self, session_id: UUID, reason: CancelReasonName) -> list[Generation]:
        """Cancel every non-cancelled speakable generation (STOP, navigation, disconnect)."""

        cancelled = []
        for generation in self._by_session.get(session_id, OrderedDict()).values():
            if generation.speakable and generation.state != "cancelled":
                self._cancel(generation, reason)
                cancelled.append(generation)
        return cancelled

    def cancel_for_request(self, session_id: UUID, request_id: UUID, reason: CancelReasonName) -> list[Generation]:
        cancelled = []
        for generation in self._by_session.get(session_id, OrderedDict()).values():
            if generation.request_id == request_id and generation.state != "cancelled":
                self._cancel(generation, reason)
                cancelled.append(generation)
        return cancelled

    def pause_active(self, session_id: UUID) -> Optional[Generation]:
        """Pause the latest speakable generation that may still be playing.

        A ``completed`` generation has sent everything but may still be
        playing locally, so it is pausable too; resuming restores it to
        ``completed`` rather than reopening output.
        """

        for generation in reversed(self._by_session.get(session_id, OrderedDict()).values()):
            if generation.speakable and generation.state in ("active", "completed"):
                generation.resume_state = generation.state
                generation.state = "paused"
                generation._deliverable.clear()
                return generation
        return None

    def resume_paused(self, session_id: UUID) -> Optional[Generation]:
        for generation in reversed(self._by_session.get(session_id, OrderedDict()).values()):
            if generation.speakable and generation.state == "paused":
                generation.state = generation.resume_state
                generation._deliverable.set()
                return generation
        return None

    def paused(self, session_id: UUID) -> Optional[Generation]:
        for generation in reversed(self._by_session.get(session_id, OrderedDict()).values()):
            if generation.state == "paused":
                return generation
        return None

    def lookup_delivered(
        self, session_id: UUID, generation_id: str, segment_id: str, sentence_id: str
    ) -> Optional[tuple[Generation, DeliveredSentence]]:
        """Return the generation and sentence an acknowledgement names, if it is eligible.

        Eligible means: the generation is known, not cancelled, and the named
        sentence was actually sent in it. Anything else returns None.
        """

        generation = self.get(session_id, generation_id)
        if generation is None or generation.state == "cancelled":
            return None
        sentence = generation.sentences.get((segment_id, sentence_id))
        if sentence is None:
            return None
        return generation, sentence

    @staticmethod
    def _cancel(generation: Generation, reason: CancelReasonName) -> None:
        if generation.state == "cancelled":
            return
        generation.state = "cancelled"
        generation.cancel_reason = reason
        generation._deliverable.set()


def new_sentence_id() -> str:
    return str(uuid4())
