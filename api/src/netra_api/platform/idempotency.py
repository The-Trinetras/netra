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

A retransmission must also be distinguished from a *reused* request_id
carrying a different logical action (an execution-clarification
requirement tightened after the initial implementation): a client that
retries the identical `next` command must replay cleanly even if the
session version has since moved on, but a request_id accidentally reused
for a different command must fail closed rather than silently apply
either interpretation. That is why the store records a payload
fingerprint alongside the result, not just the result.

A real implementation backs this with a PostgreSQL unique constraint on
(account_id, request_id) holding the serialized result and fingerprint,
written in the same transaction as the mutation it records.
"""

from __future__ import annotations

import hashlib
from typing import Any, Optional, Protocol, TypeVar
from uuid import UUID

from pydantic import BaseModel

from netra_api.platform.errors import IdempotencyConflictError, SessionVersionConflictError

TResult = TypeVar("TResult", bound=BaseModel)


class RecordedRequest(BaseModel):
    """What the store keeps for one already-applied request_id.

    payload_fingerprint is what lets a later delivery under the same
    request_id be recognised as either a genuine retry of the same
    logical action (safe to replay unconditionally) or a reused
    identifier attached to different contents (must fail, never guess
    which interpretation was intended).
    """

    payload_fingerprint: str
    result: dict[str, Any]


class IdempotencyStore(Protocol):
    """Records which request_ids have been applied, to what payload, and with what result.

    Implementations must write the recorded entry in the same
    transaction as the mutation it describes. An entry visible here for a
    mutation that did not commit would let a retry report success for
    work that never happened.
    """

    def get_recorded(self, request_id: UUID) -> Optional[RecordedRequest]:
        """Return the stored entry for an already-applied request_id, else None."""
        ...

    def record_result(self, request_id: UUID, payload_fingerprint: str, result: dict[str, Any]) -> None:
        """Store the result of applying request_id under payload_fingerprint, exactly once."""
        ...


def fingerprint_payload(payload: BaseModel) -> str:
    """Stable fingerprint of a request payload, including every field the caller supplied.

    Deliberately includes expected_session_version: a retransmission of
    the identical logical action carries the identical
    expected_session_version too, so a genuine retry and a reused
    request_id with mutated input are distinguishable by this fingerprint
    alone.
    """

    canonical = payload.model_dump_json(exclude_none=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def replay_or_conflict(
    store: IdempotencyStore, request_id: UUID, payload: BaseModel, result_model: type[TResult]
) -> Optional[TResult]:
    """Resolve request_id against the store before any version check or mutation.

    Call this first, before check_expected_version. Three outcomes:

    - No recorded entry: returns None. The caller proceeds to the normal
      expected-version check and executes the mutation.
    - Recorded entry with a matching fingerprint: returns the prior
      result. This is a genuine retry — replay it unconditionally, even
      if the session version has since moved past what this payload
      expected. Do not re-run check_expected_version for a replay: the
      mutation already happened once under whatever version applied then.
    - Recorded entry with a mismatched fingerprint: raises
      IdempotencyConflictError. request_id was reused for a different
      logical action. Do not execute either interpretation, and do not
      overwrite the original recorded entry.
    """

    recorded = store.get_recorded(request_id)
    if recorded is None:
        return None

    if recorded.payload_fingerprint != fingerprint_payload(payload):
        raise IdempotencyConflictError(str(request_id))

    return result_model.model_validate(recorded.result)


def check_expected_version(expected_version: int, actual_version: int) -> None:
    """Raise SessionVersionConflictError if a mutation's expected version is stale.

    Two devices sharing a session therefore conflict rather than
    overwriting each other: whichever applies first advances the version,
    and the second is rejected with both numbers so the client can
    resynchronise. Only called for a genuinely new request_id — a replay
    (see replay_or_conflict) skips this check entirely.
    """

    if expected_version != actual_version:
        raise SessionVersionConflictError(expected_version, actual_version)
