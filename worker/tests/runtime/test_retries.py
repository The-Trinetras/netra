from datetime import timedelta

import pytest

from netra_worker.runtime.retries import ExponentialBackoffWithJitter


def test_next_delay_grows_with_attempt_count():
    policy = ExponentialBackoffWithJitter(
        base_delay=timedelta(seconds=1), max_delay=timedelta(minutes=15), jitter_ratio=0.0
    )
    assert policy.next_delay(0) == timedelta(seconds=1)
    assert policy.next_delay(1) == timedelta(seconds=2)
    assert policy.next_delay(3) == timedelta(seconds=8)


def test_next_delay_is_capped_at_max_delay():
    policy = ExponentialBackoffWithJitter(
        base_delay=timedelta(seconds=1), max_delay=timedelta(seconds=10), jitter_ratio=0.0
    )
    assert policy.next_delay(10) == timedelta(seconds=10)


def test_next_delay_jitter_stays_within_bounds():
    policy = ExponentialBackoffWithJitter(
        base_delay=timedelta(seconds=10), max_delay=timedelta(minutes=15), jitter_ratio=0.5
    )
    for attempt in range(5):
        delay = policy.next_delay(attempt).total_seconds()
        base = min(10 * 2**attempt, 900)
        assert 0.0 <= delay <= base * 1.5


def test_negative_attempt_count_rejected():
    policy = ExponentialBackoffWithJitter()
    with pytest.raises(ValueError):
        policy.next_delay(-1)


def test_invalid_jitter_ratio_rejected():
    with pytest.raises(ValueError):
        ExponentialBackoffWithJitter(jitter_ratio=1.5)
