"""A duplicate request replays its prior result instead of re-applying it.

coordinator.md: "Return the prior result for a duplicate operation; do
not apply it again."
"""

from uuid import uuid4

import pytest

from netra_api.platform.errors import SessionVersionConflictError
from netra_api.platform.idempotency import check_expected_version, replay_recorded_result
from netra_api.session.commands import NavigationCommandName, NavigationCommandResult


class _InMemoryIdempotencyStore:
    def __init__(self):
        self._results = {}

    def get_recorded_result(self, request_id):
        return self._results.get(request_id)

    def record_result(self, request_id, result):
        self._results[request_id] = result


def test_new_request_has_nothing_to_replay():
    store = _InMemoryIdempotencyStore()
    assert replay_recorded_result(store, uuid4(), NavigationCommandResult) is None


def test_duplicate_request_replays_the_original_result():
    store = _InMemoryIdempotencyStore()
    request_id = uuid4()
    original = NavigationCommandResult(command=NavigationCommandName.NEXT, session_version=9)

    store.record_result(request_id, original.model_dump(mode="json"))
    replayed = replay_recorded_result(store, request_id, NavigationCommandResult)

    assert replayed == original


def test_duplicate_next_does_not_advance_the_version_twice():
    """The audit scenario: a redelivered "next" must leave the reading
    position where the first delivery put it."""

    store = _InMemoryIdempotencyStore()
    request_id = uuid4()
    first = NavigationCommandResult(command=NavigationCommandName.NEXT, session_version=4)
    store.record_result(request_id, first.model_dump(mode="json"))

    replayed = replay_recorded_result(store, request_id, NavigationCommandResult)

    assert replayed is not None
    assert replayed.session_version == 4


def test_stale_expected_version_conflicts_rather_than_overwriting():
    """Two devices on the same revision: the second must be rejected with
    both numbers, not silently applied."""

    with pytest.raises(SessionVersionConflictError) as excinfo:
        check_expected_version(3, 5)

    assert excinfo.value.expected_version == 3
    assert excinfo.value.actual_version == 5
