"""Idempotency and optimistic-version-check primitives shared by session and coordinator.

CLAUDE.md "Session and execution rules" requires that "Position mutations
require expected-version checks and replay-safe operation IDs" and that
"A successful state mutation advances the version; duplicate delivery
must not." coordinator.md is more specific still: "Return the prior
result for a duplicate operation; do not apply it again."

Returning the prior result is why the store holds a recorded result and
not just a set of seen request IDs. Rejecting a duplicate outright would
be wrong: a client that retries after a dropped response would be told
its command failed when it actually succeeded, and on a STOP or a
navigation command that is a worse outcome than doing nothing.

A real implementation backs this with a PostgreSQL unique constraint on
(account_id, request_id) holding the serialized result, written in the
same transaction as the mutation it records.
"""

from __future__ import annotations

from typing import Any, Optional, Protocol, TypeVar
from uuid import UUID

from pydantic import BaseModel

from netra_api.platform.errors import SessionVersionConflictError

TResult = TypeVar("TResult", bound=BaseModel)


class IdempotencyStore(Protocol):
    """Records which request_ids have been applied, and what they produced.

    Implementations must write the recorded result in the same
    transaction as the mutation it describes. A result visible here for a
    mutation that did not commit would let a retry report success for
    work that never happened.
    """

    def get_recorded_result(self, request_id: UUID) -> Optional[dict[str, Any]]:
        """Return the stored result for an already-applied request_id, else None."""
        ...

    def record_result(self, request_id: UUID, result: dict[str, Any]) -> None:
        """Store the result of applying request_id, exactly once."""
        ...


def replay_recorded_result(
    store: IdempotencyStore, request_id: UUID, result_model: type[TResult]
) -> Optional[TResult]:
    """Return the prior result for a duplicate request, or None if it is new.

    Callers apply the mutation only when this returns None, then call
    record_result with the outcome. A duplicate therefore replays the
    original result without re-applying anything and without advancing
    the session version a second time.
    """

    recorded = store.get_recorded_result(request_id)
    if recorded is None:
        return None
    return result_model.model_validate(recorded)


def check_expected_version(expected_version: int, actual_version: int) -> None:
    """Raise SessionVersionConflictError if a mutation's expected version is stale.

    Two devices sharing a session therefore conflict rather than
    overwriting each other: whichever applies first advances the version,
    and the second is rejected with both numbers so the client can
    resynchronise.
    """

    if expected_version != actual_version:
        raise SessionVersionConflictError(expected_version, actual_version)
