"""Worker adapters for M3's ports: mapping, citable gating and lease fencing."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from netra_worker.jobs.multimedia.extraction import (
    ExtractedObjectKind,
    ExtractionOutcome,
    ValidationVerdict,
)
from netra_worker.runtime.errors import LeaseLostError
from netra_worker.runtime.job_repository import Job
from netra_worker.runtime.multimedia_sinks import (
    JobStageRecorder,
    PostgresExtractionCandidateSink,
    PostgresProviderBindingSink,
)


class _Store:
    def __init__(self):
        self.calls = []

    async def store_candidate(self, **kwargs):
        self.calls.append(("candidate", kwargs))

    async def bind_provider(self, **kwargs):
        self.calls.append(("bind", kwargs))


def _outcome(verified: bool, checked: int) -> ExtractionOutcome:
    return ExtractionOutcome(kind=ExtractedObjectKind.EQUATION, object_index=1, structure={"root": "x"},
                             validation=ValidationVerdict(source_verified=verified, checked_count=checked),
                             findings=[{"part_id": "p"}])


@pytest.mark.parametrize("verified,checked,expected", [(True, 2, True), (True, 0, False), (False, 3, False)])
async def test_extraction_sink_passes_citable_and_an_independent_verdict(verified, checked, expected):
    store = _Store()
    version = uuid4()
    await PostgresExtractionCandidateSink(store).store_candidate(
        source_version_id=version, outcome=_outcome(verified, checked), citable=expected, idempotency_key="k")
    [(kind, call)] = store.calls
    assert kind == "candidate"
    assert call["kind"] == "equation" and call["object_index"] == 1
    assert call["citable"] is expected and call["verified"] is expected
    assert call["source_version_id"] == version and call["findings"] == [{"part_id": "p"}]


async def test_binding_sink_forwards_provider_identity_unchanged():
    store = _Store()
    video = uuid4()
    await PostgresProviderBindingSink(store).bind(video_id=video, provider="twelvelabs", provider_index_id="i",
                                                  provider_video_id="a", model_name="pegasus", model_version="1.2")
    assert store.calls == [("bind", dict(video_id=video, provider="twelvelabs", provider_index_id="i",
                                         provider_video_id="a", model_name="pegasus", model_version="1.2"))]


async def test_stage_recorder_refuses_to_record_without_a_lease():
    now = datetime.now(timezone.utc)
    job = Job(job_id=uuid4(), job_type="extract_equation", payload={}, next_run_at=now,
              created_at=now, updated_at=now, lease=None)

    def no_sessions():
        raise AssertionError("no session may be opened without a lease")

    with pytest.raises(LeaseLostError):
        await JobStageRecorder(no_sessions, job).record("extract_object", None)
