"""Non-durable in-process session repositories.

For unit tests and labelled fixture journeys only. Production composition
(netra_api.bootstrap) never registers these: a restart would silently forget
positions, versions and replay records, which is exactly the "fake successful
persistence" CLAUDE.md forbids. Their atomicity (one asyncio lock per store)
mirrors the PostgreSQL contract so service logic is exercised faithfully.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from netra_api.platform.errors import SessionVersionConflictError
from netra_api.platform.idempotency import RecordedRequest
from netra_api.session.dialogue import DialogueEntry
from netra_api.session.repository import RequestAlreadyRecordedError
from netra_api.session.result_sets import ResultSet
from netra_api.session.state import SessionState


class InMemorySessionRepository:
    def __init__(self) -> None:
        self._states: dict[UUID, SessionState] = {}
        self._records: dict[tuple[UUID, UUID], RecordedRequest] = {}
        self._lock = asyncio.Lock()
        self.commit_count = 0

    async def get(self, session_id: UUID) -> Optional[SessionState]:
        return self._states.get(session_id)

    async def create(self, state: SessionState) -> SessionState:
        async with self._lock:
            if state.session_id in self._states:
                raise ValueError("session already exists")
            self._states[state.session_id] = state
            return state

    async def get_recorded(self, account_id: UUID, request_id: UUID) -> Optional[RecordedRequest]:
        return self._records.get((account_id, request_id))

    async def commit(
        self,
        *,
        account_id: UUID,
        session_id: UUID,
        request_id: Optional[UUID],
        payload_fingerprint: Optional[str],
        result: Optional[dict[str, Any]],
        expected_version: int,
        new_state: Optional[SessionState],
    ) -> Optional[SessionState]:
        async with self._lock:
            if request_id is not None:
                existing = self._records.get((account_id, request_id))
                if existing is not None:
                    raise RequestAlreadyRecordedError(existing)

            if new_state is not None:
                stored = self._states.get(session_id)
                actual = stored.session_version if stored is not None else -1
                if actual != expected_version:
                    raise SessionVersionConflictError(expected_version, max(actual, 0))
                if new_state.session_version != expected_version + 1:
                    raise ValueError("new_state must advance the version by exactly one")
                self._states[session_id] = new_state

            if request_id is not None:
                self._records[(account_id, request_id)] = RecordedRequest(
                    payload_fingerprint=payload_fingerprint or "", result=result or {}
                )
            self.commit_count += 1
            return new_state


class InMemoryResultSetRepository:
    def __init__(self) -> None:
        self._sets: dict[UUID, ResultSet] = {}

    async def create(self, result_set: ResultSet) -> ResultSet:
        self._sets[result_set.result_set_id] = result_set
        return result_set

    async def get(self, session_id: UUID, result_set_id: UUID, now: datetime) -> Optional[ResultSet]:
        found = self._sets.get(result_set_id)
        if found is None or found.session_id != session_id or found.expires_at <= now:
            return None
        return found


class InMemoryDialogueLog:
    def __init__(self) -> None:
        self._entries: list[DialogueEntry] = []
        self._keys: set[tuple[UUID, int]] = set()

    async def append(self, entries: list[DialogueEntry]) -> None:
        for entry in entries:
            key = (entry.request_id, entry.ordinal)
            if key in self._keys:
                continue
            self._keys.add(key)
            self._entries.append(entry)

    async def recent(self, session_id: UUID, limit: int) -> list[DialogueEntry]:
        mine = [entry for entry in self._entries if entry.session_id == session_id]
        return mine[-limit:] if limit > 0 else []
