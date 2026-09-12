"""A duplicate request replays its prior result instead of re-applying it.

coordinator.md: "Return the prior result for a duplicate operation; do
not apply it again." Tightened by the idempotent-retry-ordering execution
clarification: a request_id reused for a *different* logical action must
fail closed, never replay the wrong result or silently re-execute.
"""

from uuid import uuid4

import pytest

from netra_api.platform.errors import IdempotencyConflictError, SessionVersionConflictError
from netra_api.platform.idempotency import (
    RecordedRequest,
    check_expected_version,
    fingerprint_payload,
    replay_or_conflict,
)
from netra_api.session.commands import NavigationCommandName, NavigationCommandRequest, NavigationCommandResult


class _InMemoryIdempotencyStore:
    def __init__(self):
        self._entries: dict = {}

    def get_recorded(self, request_id):
        return self._entries.get(request_id)

    def record_result(self, request_id, payload_fingerprint, result):
        self._entries[request_id] = RecordedRequest(payload_fingerprint=payload_fingerprint, result=result)


def _next_command(expected_session_version=4):
    return NavigationCommandRequest(
        command=NavigationCommandName.NEXT, expected_session_version=expected_session_version
    )


def test_new_request_has_nothing_to_replay():
    store = _InMemoryIdempotencyStore()
    assert replay_or_conflict(store, uuid4(), _next_command(), NavigationCommandResult) is None


def test_duplicate_request_replays_the_original_result():
    store = _InMemoryIdempotencyStore()
    request_id = uuid4()
    command = _next_command()
    original = NavigationCommandResult(command=NavigationCommandName.NEXT, session_version=9)

    store.record_result(request_id, fingerprint_payload(command), original.model_dump(mode="json"))
    replayed = replay_or_conflict(store, request_id, command, NavigationCommandResult)

    assert replayed == original


def test_duplicate_next_does_not_advance_the_version_twice():
    """The audit scenario: a redelivered "next" must leave the reading
    position where the first delivery put it."""

    store = _InMemoryIdempotencyStore()
    request_id = uuid4()
    command = _next_command()
    first = NavigationCommandResult(command=NavigationCommandName.NEXT, session_version=4)
    store.record_result(request_id, fingerprint_payload(command), first.model_dump(mode="json"))

    replayed = replay_or_conflict(store, request_id, command, NavigationCommandResult)

    assert replayed is not None
    assert replayed.session_version == 4


def test_replay_ignores_a_now_stale_expected_version():
    """A genuine retry replays unconditionally, even though the session
    has since moved past what this payload originally expected — the
    idempotent-retry-ordering clarification's central requirement."""

    store = _InMemoryIdempotencyStore()
    request_id = uuid4()
    command = _next_command(expected_session_version=4)
    original = NavigationCommandResult(command=NavigationCommandName.NEXT, session_version=5)
    store.record_result(request_id, fingerprint_payload(command), original.model_dump(mode="json"))

    # Same request_id, same exact payload, delivered again after the
    # session has since moved to version 9 for unrelated reasons.
    replayed = replay_or_conflict(store, request_id, command, NavigationCommandResult)

    assert replayed == original


def test_reused_request_id_with_a_different_command_fails_closed():
    """request_id reused for a different logical action must not replay
    the old result, must not execute the new one, and must not overwrite
    the original record."""

    store = _InMemoryIdempotencyStore()
    request_id = uuid4()
    first_command = _next_command()
    store.record_result(
        request_id,
        fingerprint_payload(first_command),
        NavigationCommandResult(command=NavigationCommandName.NEXT, session_version=4).model_dump(mode="json"),
    )

    different_command = NavigationCommandRequest(
        command=NavigationCommandName.PREVIOUS, expected_session_version=4
    )

    with pytest.raises(IdempotencyConflictError):
        replay_or_conflict(store, request_id, different_command, NavigationCommandResult)

    # The original record is untouched.
    assert store.get_recorded(request_id).payload_fingerprint == fingerprint_payload(first_command)


def test_reused_request_id_with_a_different_expected_version_fails_closed():
    """expected_session_version is part of the logical action: a request_id
    replayed with a mutated version number is not a faithful retry."""

    store = _InMemoryIdempotencyStore()
    request_id = uuid4()
    command = _next_command(expected_session_version=4)
    store.record_result(
        request_id,
        fingerprint_payload(command),
        NavigationCommandResult(command=NavigationCommandName.NEXT, session_version=5).model_dump(mode="json"),
    )

    mutated = _next_command(expected_session_version=5)

    with pytest.raises(IdempotencyConflictError):
        replay_or_conflict(store, request_id, mutated, NavigationCommandResult)


def test_stale_expected_version_conflicts_rather_than_overwriting():
    """Two devices on the same revision: the second must be rejected with
    both numbers, not silently applied."""

    with pytest.raises(SessionVersionConflictError) as excinfo:
        check_expected_version(3, 5)

    assert excinfo.value.expected_version == 3
    assert excinfo.value.actual_version == 5
