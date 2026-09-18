"""Transaction ownership for repositories that share a caller's AsyncSession.

M2 repositories read through SQLAlchemy's autobegin and then open their own
short write transaction. Closing that read transaction must never publish
(commit) or silently discard (roll back) writes that another operation left on
the same session: that would break the caller's atomicity, for example an
assessment attempt, its pending-question change and its outbox event.

``close_read_only_transaction`` therefore commits only a transaction that has
performed no writes. Writes are detected from pending ORM state and from a
session-level flag set by every flush and every non-SELECT statement executed
through the session; the flag clears when the root transaction ends. Anything
else fails closed with ``ForeignTransactionError`` and leaves the session
untouched for its owner to commit or roll back.
"""

from __future__ import annotations

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, SessionTransaction

_WROTE = "netra.transaction_wrote"


class ForeignTransactionError(RuntimeError):
    """The shared session holds another operation's uncommitted writes."""


@event.listens_for(Session, "do_orm_execute")
def _mark_statement_write(state) -> None:
    if not state.is_select:
        state.session.info[_WROTE] = True


@event.listens_for(Session, "after_flush")
def _mark_flush_write(session: Session, _flush_context) -> None:
    session.info[_WROTE] = True


@event.listens_for(Session, "after_transaction_end")
def _clear_write_mark(session: Session, transaction: SessionTransaction) -> None:
    if transaction.parent is None:
        session.info.pop(_WROTE, None)


def has_pending_writes(session: AsyncSession) -> bool:
    sync = session.sync_session
    return bool(sync.new or sync.dirty or sync.deleted or sync.info.get(_WROTE))


async def close_read_only_transaction(session: AsyncSession) -> None:
    """End a read-only autobegin before the caller opens its own transaction.

    Raises ForeignTransactionError, without committing or rolling back, when
    the open transaction (or the unit of work) contains writes.
    """

    if not session.in_transaction() and not has_pending_writes(session):
        return
    if has_pending_writes(session):
        raise ForeignTransactionError(
            "refusing to open a repository transaction over another operation's uncommitted writes"
        )
    await session.commit()


__all__ = ["ForeignTransactionError", "close_read_only_transaction", "has_pending_writes"]
