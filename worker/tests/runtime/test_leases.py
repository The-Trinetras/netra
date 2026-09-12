from datetime import datetime, timedelta, timezone
from uuid import uuid4

from netra_worker.runtime.leases import Lease


def test_lease_not_expired_before_expiry():
    now = datetime.now(timezone.utc)
    lease = Lease(token=uuid4(), worker_id="worker-1", expires_at=now + timedelta(seconds=30))
    assert lease.is_expired(now) is False


def test_lease_expired_after_expiry():
    now = datetime.now(timezone.utc)
    lease = Lease(token=uuid4(), worker_id="worker-1", expires_at=now - timedelta(seconds=1))
    assert lease.is_expired(now) is True


def test_lease_expired_at_exact_boundary():
    now = datetime.now(timezone.utc)
    lease = Lease(token=uuid4(), worker_id="worker-1", expires_at=now)
    assert lease.is_expired(now) is True
