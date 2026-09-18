"""Retry/backoff policy for worker job re-attempts.

CLAUDE.md "Background jobs" requires exponential backoff with jitter;
this module implements the pure calculation so JobRepository.fail(...)
callers can compute next_run_at without duplicating the formula. This
is deterministic local math, not a provider call, so it is implemented
in full rather than stubbed.
"""

from __future__ import annotations

import random
from datetime import timedelta
from typing import Protocol


class BackoffPolicy(Protocol):
    def next_delay(self, attempt_count: int) -> timedelta:
        ...


class ExponentialBackoffWithJitter:
    """delay = min(max_delay, base_delay * 2**attempt_count) +/- jitter_ratio."""

    def __init__(
        self,
        base_delay: timedelta = timedelta(seconds=1),
        max_delay: timedelta = timedelta(minutes=15),
        jitter_ratio: float = 0.2,
    ) -> None:
        if not 0.0 <= jitter_ratio <= 1.0:
            raise ValueError("jitter_ratio must be between 0 and 1")
        self._base_delay = base_delay
        self._max_delay = max_delay
        self._jitter_ratio = jitter_ratio

    def next_delay(self, attempt_count: int) -> timedelta:
        if attempt_count < 0:
            raise ValueError("attempt_count must be >= 0")
        # Cap the exponent so a large attempt count cannot overflow timedelta.
        exponential = self._base_delay * (2 ** min(attempt_count, 32))
        capped_seconds = min(exponential, self._max_delay).total_seconds()
        jitter_seconds = capped_seconds * self._jitter_ratio
        jittered_seconds = capped_seconds + random.uniform(-jitter_seconds, jitter_seconds)
        return timedelta(seconds=max(0.0, jittered_seconds))
