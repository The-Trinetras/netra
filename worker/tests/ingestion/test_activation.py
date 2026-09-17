from uuid import uuid4

import pytest

from netra_worker.jobs.ingestion.activate_version import ActivateVersionJob, ActivateVersionPayload


class Store:
    def __init__(self):
        self.calls = []

    async def activate_version_internal(self, source_id, source_version_id, expected_version_number):
        self.calls.append((source_id, source_version_id, expected_version_number))


@pytest.mark.asyncio
async def test_activation_job_delegates_explicit_identity_and_expectation():
    store = Store()
    source_id, version_id = uuid4(), uuid4()
    payload = ActivateVersionPayload(source_id=source_id, source_version_id=version_id,
                                     expected_version_number=4, idempotency_key="activate-4")
    await ActivateVersionJob(store).handle(payload)
    assert store.calls == [(source_id, version_id, 4)]
