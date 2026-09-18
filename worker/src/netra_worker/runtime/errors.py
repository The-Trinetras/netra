"""Failure classes shared by worker handlers, repositories and the dispatcher.

backend-data.md: "Do not retry denied access, invalid input or exhausted
quota indiscriminately." Handlers raise ``PermanentJobError`` for those; the
dispatcher dead-letters instead of scheduling another attempt. A lost lease
is neither a success nor a retryable failure of *this* worker: the current
owner (or the next claimant) decides the outcome.
"""

from __future__ import annotations


class PermanentJobError(RuntimeError):
    """A job failure that must be dead-lettered without another attempt."""


class JobCancelled(RuntimeError):
    """The job was deliberately cancelled (not failed, not lease-lost).

    Recorded as a terminal ``cancelled`` outcome, never retried and never
    dead-lettered as a failure: nobody wants its result any more. Losing
    the lease is NOT cancellation; raise ``LeaseLostError`` for that so the
    next claimant resumes from the recorded stages.
    """


class LeaseLostError(RuntimeError):
    """This worker no longer owns the job/outbox claim it was acting on.

    Raised by repositories when a fenced write (complete, fail, heartbeat,
    record_stage, mark_processed) finds a different or expired lease, so a
    worker that lost ownership can never commit as the owner.
    """


__all__ = ["JobCancelled", "LeaseLostError", "PermanentJobError"]
