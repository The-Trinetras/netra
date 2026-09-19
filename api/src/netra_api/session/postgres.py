"""PostgreSQL session repositories (SQLAlchemy Core over postgresql+asyncpg).

Tables are created by migration 0005 (M2-serialized); nothing here creates
schema. The atomic commit uses one short transaction: an INSERT into
``session_request_records`` whose primary key (account_id, request_id) makes
concurrent identical retransmissions collapse into one effect, then a
conditional ``UPDATE ... WHERE session_version = :expected``.

No external call ever runs inside these transactions.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import Column, DateTime, Integer, String, Table, Text, and_, insert, select, update
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from netra_api.platform.database import M1_METADATA
from netra_api.platform.errors import SessionVersionConflictError
from netra_api.platform.idempotency import RecordedRequest
from netra_api.session.dialogue import DialogueEntry
from netra_api.session.repository import RequestAlreadyRecordedError
from netra_api.session.result_sets import ResultSet
from netra_api.session.state import SessionState

sessions = Table(
    "sessions",
    M1_METADATA,
    Column("session_id", PGUUID(as_uuid=True), primary_key=True),
    Column("account_id", PGUUID(as_uuid=True), nullable=False, index=True),
    Column("session_version", Integer, nullable=False),
    Column("state", JSONB, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

session_request_records = Table(
    "session_request_records",
    M1_METADATA,
    Column("account_id", PGUUID(as_uuid=True), primary_key=True),
    Column("request_id", PGUUID(as_uuid=True), primary_key=True),
    Column("session_id", PGUUID(as_uuid=True), nullable=False, index=True),
    Column("payload_fingerprint", String(64), nullable=False),
    Column("result", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

session_result_sets = Table(
    "session_result_sets",
    M1_METADATA,
    Column("result_set_id", PGUUID(as_uuid=True), primary_key=True),
    Column("session_id", PGUUID(as_uuid=True), nullable=False, index=True),
    Column("source_version_id", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("items", JSONB, nullable=False),
)

session_dialogue_entries = Table(
    "session_dialogue_entries",
    M1_METADATA,
    Column("request_id", PGUUID(as_uuid=True), primary_key=True),
    Column("ordinal", Integer, primary_key=True),
    Column("session_id", PGUUID(as_uuid=True), nullable=False, index=True),
    Column("role", String(16), nullable=False),
    Column("content", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)


class PostgresSessionRepository:
    def __init__(self, engine) -> None:
        self._engine = engine

    async def get(self, session_id: UUID) -> Optional[SessionState]:
        statement = select(sessions.c.state).where(sessions.c.session_id == session_id)
        async with self._engine.connect() as connection:
            row = (await connection.execute(statement)).first()
        return SessionState.model_validate(row[0]) if row else None

    async def create(self, state: SessionState) -> SessionState:
        async with self._engine.begin() as connection:
            await connection.execute(
                insert(sessions).values(
                    session_id=state.session_id,
                    account_id=state.account.account_id,
                    session_version=state.session_version,
                    state=state.model_dump(mode="json"),
                    updated_at=state.updated_at,
                )
            )
        return state

    async def get_recorded(self, account_id: UUID, request_id: UUID) -> Optional[RecordedRequest]:
        statement = select(session_request_records.c.payload_fingerprint, session_request_records.c.result).where(
            and_(
                session_request_records.c.account_id == account_id,
                session_request_records.c.request_id == request_id,
            )
        )
        async with self._engine.connect() as connection:
            row = (await connection.execute(statement)).first()
        return RecordedRequest(payload_fingerprint=row[0], result=row[1]) if row else None

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
        if new_state is not None and new_state.session_version != expected_version + 1:
            raise ValueError("new_state must advance the version by exactly one")

        try:
            async with self._engine.begin() as connection:
                # Claim the request identity BEFORE the conditional version
                # update, exactly as the in-memory contract checks the replay
                # record first. A concurrent identical retransmission then
                # waits on this primary key and, once the winner commits,
                # fails with IntegrityError → RequestAlreadyRecordedError (its
                # recorded result), instead of losing the version race and
                # being told SESSION_VERSION_CONFLICT. A later version
                # conflict rolls this insert back with the transaction.
                if request_id is not None:
                    await connection.execute(
                        insert(session_request_records).values(
                            account_id=account_id,
                            request_id=request_id,
                            session_id=session_id,
                            payload_fingerprint=payload_fingerprint,
                            result=result,
                            created_at=_now(),
                        )
                    )
                if new_state is not None:
                    updated = await connection.execute(
                        update(sessions)
                        .where(
                            and_(
                                sessions.c.session_id == session_id,
                                sessions.c.account_id == account_id,
                                sessions.c.session_version == expected_version,
                            )
                        )
                        .values(
                            session_version=new_state.session_version,
                            state=new_state.model_dump(mode="json"),
                            updated_at=new_state.updated_at,
                        )
                    )
                    if updated.rowcount != 1:
                        actual = (
                            await connection.execute(
                                select(sessions.c.session_version).where(sessions.c.session_id == session_id)
                            )
                        ).scalar()
                        raise SessionVersionConflictError(expected_version, int(actual or 0))
        except IntegrityError:
            if request_id is None:
                raise
            recorded = await self.get_recorded(account_id, request_id)
            if recorded is None:
                raise
            raise RequestAlreadyRecordedError(recorded)
        return new_state


class PostgresResultSetRepository:
    def __init__(self, engine) -> None:
        self._engine = engine

    async def create(self, result_set: ResultSet) -> ResultSet:
        values = result_set.model_dump(mode="json")
        values["result_set_id"] = result_set.result_set_id
        values["session_id"] = result_set.session_id
        values["created_at"] = result_set.created_at
        values["expires_at"] = result_set.expires_at
        async with self._engine.begin() as connection:
            await connection.execute(insert(session_result_sets).values(**values))
        return result_set

    async def get(self, session_id: UUID, result_set_id: UUID, now: datetime) -> Optional[ResultSet]:
        statement = select(session_result_sets).where(
            and_(
                session_result_sets.c.result_set_id == result_set_id,
                session_result_sets.c.session_id == session_id,
                session_result_sets.c.expires_at > now,
            )
        )
        async with self._engine.connect() as connection:
            row = (await connection.execute(statement)).mappings().first()
        return ResultSet.model_validate(dict(row)) if row else None


class PostgresDialogueLog:
    def __init__(self, engine) -> None:
        self._engine = engine

    async def append(self, entries: list[DialogueEntry]) -> None:
        if not entries:
            return
        statement = (
            pg_insert(session_dialogue_entries)
            .values([entry.model_dump() for entry in entries])
            .on_conflict_do_nothing(index_elements=["request_id", "ordinal"])
        )
        async with self._engine.begin() as connection:
            await connection.execute(statement)

    async def recent(self, session_id: UUID, limit: int) -> list[DialogueEntry]:
        if limit <= 0:
            return []
        statement = (
            select(session_dialogue_entries)
            .where(session_dialogue_entries.c.session_id == session_id)
            .order_by(session_dialogue_entries.c.created_at.desc(), session_dialogue_entries.c.ordinal.desc())
            .limit(limit)
        )
        async with self._engine.connect() as connection:
            rows = (await connection.execute(statement)).mappings().all()
        return [DialogueEntry.model_validate(dict(row)) for row in reversed(rows)]


def _now() -> datetime:
    return datetime.now(timezone.utc)
