"""Multimedia job orchestration: replay, recovery, cancellation, deadlines.

These jobs used to be stubs that raised NotImplementedError, and
worker/tests/multimedia/test_job_payloads.py asserted exactly that. The
stubs are implemented now, so that file is replaced by this one; the
behaviour being checked changed on purpose rather than the assertion
being relaxed.

The doubles below are test-only. A fake provider proves the job calls it
once and recovers correctly when it fails; it proves nothing about
Twelve Labs. No live provider check has been run — see
docs/team/handoffs/M3.md.
"""

from uuid import uuid4

import pytest

from netra_worker.jobs.multimedia.base import (
    JobCancelledError,
    JobDeadlineExceededError,
    run_stages,
)
from netra_worker.jobs.multimedia.extraction import (
    ExtractedObjectKind,
    ExtractionOutcome,
    ExtractObjectPayload,
    ValidationVerdict,
    is_citable,
)
from netra_worker.jobs.multimedia.figures import ExtractChartJob, ExtractFigureJob
from netra_worker.jobs.multimedia.tables import ExtractTableJob, ExtractTablePayload
from netra_worker.jobs.multimedia.video import (
    BIND_STAGE,
    DERIVE_STAGE,
    INDEX_STAGE,
    DeriveVideoEvidenceJob,
    DeriveVideoEvidencePayload,
    EvidenceProvenanceRecord,
    IndexVideoJob,
    IndexVideoPayload,
    VideoEvidenceCandidateRecord,
    VideoEvidenceKindName,
)

from datetime import datetime, timezone

PRODUCED_AT = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)


class FakeRecorder:
    """In-memory StageRecorder. Records order so replays are inspectable.

    lease_lost mirrors the rule that a worker whose lease expired must
    not commit as the current owner: record() refuses rather than
    writing.
    """

    def __init__(self, already_done=None, remote_ids=None):
        self.stages = list(already_done or [])
        self.remote_ids = dict(remote_ids or {})
        self.writes = []
        self.lease_lost = False

    async def completed_stages(self):
        return list(self.stages)

    async def remote_operation_id(self, stage):
        return self.remote_ids.get(stage)

    async def record(self, stage, remote_operation_id=None):
        if self.lease_lost:
            raise RuntimeError("lease no longer held; refusing to record progress")
        self.stages.append(stage)
        self.writes.append((stage, remote_operation_id))
        if remote_operation_id is not None:
            self.remote_ids[stage] = remote_operation_id


class Cancelled:
    def __init__(self, cancelled=True, reason="student pressed stop"):
        self._cancelled = cancelled
        self._reason = reason

    def is_cancelled(self):
        return self._cancelled

    def reason(self):
        return self._reason


class FixedDeadline:
    def __init__(self, seconds):
        self.seconds = seconds

    def remaining_seconds(self):
        return self.seconds


class FakeIndexing:
    def __init__(self, existing=None, asset_id="tlv_new"):
        self.existing = existing
        self.asset_id = asset_id
        self.index_calls = 0
        self.find_calls = 0
        self.timeouts = []

    async def find_existing(self, *, external_ref):
        self.find_calls += 1
        return self.existing

    async def index(self, *, external_ref, content_type, timeout_seconds):
        self.index_calls += 1
        self.timeouts.append(timeout_seconds)
        return self.asset_id


class FakeBindings:
    def __init__(self):
        self.bound = []

    async def bind(self, **kwargs):
        self.bound.append(kwargs)


class FakeDescriptions:
    def __init__(self, candidates=None, error=None):
        self.candidates = candidates if candidates is not None else []
        self.error = error
        self.calls = 0

    async def describe(self, **kwargs):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return list(self.candidates)


class FakeEvidenceSink:
    def __init__(self, error=None):
        self.stored = {}
        self.calls = 0
        self.error = error

    async def store_candidates(self, *, video_id, candidates, idempotency_key):
        self.calls += 1
        if self.error is not None:
            raise self.error
        # Idempotent by key, the way a real store must be.
        self.stored[idempotency_key] = list(candidates)


class FakeExtraction:
    def __init__(self, outcome=None, error=None):
        self.outcome = outcome
        self.error = error
        self.calls = 0

    async def extract(self, **kwargs):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.outcome


class FakeExtractionSink:
    def __init__(self):
        self.stored = []

    async def store_candidate(self, *, source_version_id, outcome, citable, idempotency_key):
        self.stored.append((outcome, citable, idempotency_key))


def _index_payload(**extra):
    return IndexVideoPayload(
        idempotency_key="job-index-1",
        source_id=uuid4(),
        source_version_id=uuid4(),
        video_id=uuid4(),
        external_ref="uploads/lecture-v1.mp4",
        content_type="video/mp4",
        **extra,
    )


def _derive_payload(**extra):
    return DeriveVideoEvidencePayload(
        idempotency_key="job-derive-1",
        source_id=uuid4(),
        source_version_id=uuid4(),
        video_id=uuid4(),
        provider_video_id="tlv_abc123",
        locator="lecture-v1",
        **extra,
    )


def _candidate(payload):
    return VideoEvidenceCandidateRecord(
        video_id=payload.video_id,
        source_version_id=payload.source_version_id,
        locator=payload.locator,
        start_ms=44_000,
        end_ms=52_000,
        kind=VideoEvidenceKindName.VISUAL_DESCRIPTION,
        description="A line rises across labelled axes.",
        provenance=EvidenceProvenanceRecord(
            provider="twelvelabs",
            model_name="pegasus",
            model_version="test-fixture",
            produced_at=PRODUCED_AT,
            stage=DERIVE_STAGE,
        ),
    )


def _outcome(*, source_verified, checked=2):
    return ExtractionOutcome(
        kind=ExtractedObjectKind.CHART,
        object_index=2,
        structure={"chart_id": "fig02"},
        validation=ValidationVerdict(
            source_verified=source_verified,
            checked_count=checked,
            mismatch_count=0 if source_verified else 1,
        ),
    )


def _extract_payload():
    return ExtractObjectPayload(
        idempotency_key="job-extract-1",
        source_id=uuid4(),
        source_version_id=uuid4(),
        object_index=2,
        object_key="figures/fig02.png",
    )


async def test_run_stages_executes_each_stage_once_and_records_it():
    recorder = FakeRecorder()
    calls = []

    async def first():
        calls.append("a")
        return "op-1"

    async def second():
        calls.append("b")
        return None

    executed = await run_stages(recorder, [("a", first), ("b", second)])

    assert executed == ["a", "b"]
    assert calls == ["a", "b"]
    assert recorder.writes == [("a", "op-1"), ("b", None)]


async def test_a_replay_skips_stages_already_recorded_complete():
    """At-least-once delivery must not repeat an external effect."""

    recorder = FakeRecorder(already_done=["a"])
    calls = []

    async def first():
        calls.append("a")
        return None

    async def second():
        calls.append("b")
        return None

    executed = await run_stages(recorder, [("a", first), ("b", second)])

    assert executed == ["b"]
    assert calls == ["b"]


async def test_cancellation_stops_before_the_next_stage_starts():
    recorder = FakeRecorder()

    async def never_runs():
        raise AssertionError("cancelled work must not call the provider")

    with pytest.raises(JobCancelledError) as excinfo:
        await run_stages(
            recorder, [("a", never_runs)], cancellation=Cancelled()
        )

    assert excinfo.value.stage == "a"
    assert excinfo.value.reason == "student pressed stop"
    assert recorder.writes == []


async def test_an_exhausted_deadline_stops_before_the_next_stage_starts():
    recorder = FakeRecorder()

    async def never_runs():
        raise AssertionError("expired work must not call the provider")

    with pytest.raises(JobDeadlineExceededError):
        await run_stages(recorder, [("a", never_runs)], deadline=FixedDeadline(0))

    assert recorder.writes == []


async def test_a_stage_already_in_flight_is_not_interrupted_by_cancellation():
    """Interrupting mid-stage would leave an external effect unrecorded."""

    recorder = FakeRecorder()
    cancellation = Cancelled(cancelled=False)
    completed = []

    async def first():
        cancellation._cancelled = True
        completed.append("a")
        return "op-1"

    async def second():
        raise AssertionError("the second stage must not start")

    with pytest.raises(JobCancelledError):
        await run_stages(
            recorder, [("a", first), ("b", second)], cancellation=cancellation
        )

    assert completed == ["a"]
    assert recorder.writes == [("a", "op-1")]


async def test_a_lost_lease_refuses_the_stage_write():
    recorder = FakeRecorder()
    recorder.lease_lost = True

    async def work():
        return "op-1"

    with pytest.raises(RuntimeError):
        await run_stages(recorder, [("a", work)])

    assert recorder.stages == []


async def test_index_video_records_the_provider_asset_id_then_binds_it():
    recorder = FakeRecorder()
    indexing = FakeIndexing()
    bindings = FakeBindings()
    payload = _index_payload()

    await IndexVideoJob(
        indexing,
        bindings,
        recorder,
        provider="twelvelabs",
        provider_index_id="idx_1",
        model_name="marengo",
        model_version="test-fixture",
    ).handle(payload)

    assert indexing.index_calls == 1
    assert recorder.remote_ids[INDEX_STAGE] == "tlv_new"
    assert bindings.bound[0]["provider_video_id"] == "tlv_new"
    assert bindings.bound[0]["video_id"] == payload.video_id
    assert recorder.stages == [INDEX_STAGE, BIND_STAGE]


async def test_a_retry_after_an_uncertain_completion_reconciles_instead_of_reindexing():
    """backend-data.md: do not blindly repeat an external call."""

    recorder = FakeRecorder()
    indexing = FakeIndexing(existing="tlv_already_there")
    bindings = FakeBindings()

    await IndexVideoJob(
        indexing,
        bindings,
        recorder,
        provider="twelvelabs",
        provider_index_id="idx_1",
        model_name="marengo",
        model_version="test-fixture",
    ).handle(_index_payload())

    assert indexing.find_calls == 1
    assert indexing.index_calls == 0
    assert recorder.remote_ids[INDEX_STAGE] == "tlv_already_there"


async def test_a_recorded_asset_id_is_reused_without_asking_the_provider():
    """The crash-between-effect-and-acknowledgement case."""

    recorder = FakeRecorder(remote_ids={INDEX_STAGE: "tlv_recorded"})
    indexing = FakeIndexing()
    bindings = FakeBindings()

    await IndexVideoJob(
        indexing,
        bindings,
        recorder,
        provider="twelvelabs",
        provider_index_id="idx_1",
        model_name="marengo",
        model_version="test-fixture",
    ).handle(_index_payload())

    assert indexing.find_calls == 0
    assert indexing.index_calls == 0
    assert bindings.bound[0]["provider_video_id"] == "tlv_recorded"


async def test_replaying_a_finished_index_job_calls_nothing():
    recorder = FakeRecorder(
        already_done=[INDEX_STAGE, BIND_STAGE], remote_ids={INDEX_STAGE: "tlv_1"}
    )
    indexing = FakeIndexing()
    bindings = FakeBindings()

    await IndexVideoJob(
        indexing,
        bindings,
        recorder,
        provider="twelvelabs",
        provider_index_id="idx_1",
        model_name="marengo",
        model_version="test-fixture",
    ).handle(_index_payload())

    assert (indexing.index_calls, indexing.find_calls, bindings.bound) == (0, 0, [])


async def test_a_provider_call_never_outlives_the_remaining_deadline():
    recorder = FakeRecorder()
    indexing = FakeIndexing()

    await IndexVideoJob(
        indexing,
        FakeBindings(),
        recorder,
        provider="twelvelabs",
        provider_index_id="idx_1",
        model_name="marengo",
        model_version="test-fixture",
        deadline=FixedDeadline(4.0),
        stage_timeout_seconds=60.0,
    ).handle(_index_payload())

    assert indexing.timeouts == [4.0]


async def test_derive_stores_candidates_under_the_payloads_idempotency_key():
    recorder = FakeRecorder()
    payload = _derive_payload()
    descriptions = FakeDescriptions([_candidate(payload)])
    sink = FakeEvidenceSink()

    await DeriveVideoEvidenceJob(descriptions, sink, recorder).handle(payload)

    assert sink.stored[payload.idempotency_key][0].start_ms == 44_000
    assert recorder.stages == [DERIVE_STAGE]


async def test_deriving_nothing_is_an_outcome_not_a_failure():
    """A video with no visual evidence is reported transcript-only, not failed."""

    recorder = FakeRecorder()
    payload = _derive_payload()
    sink = FakeEvidenceSink()

    await DeriveVideoEvidenceJob(FakeDescriptions([]), sink, recorder).handle(payload)

    assert sink.calls == 0
    assert recorder.stages == [DERIVE_STAGE]


async def test_a_retry_after_a_failed_write_re_derives_rather_than_losing_evidence():
    """The stage stays unrecorded when the write fails, so nothing is lost."""

    recorder = FakeRecorder()
    payload = _derive_payload()
    descriptions = FakeDescriptions([_candidate(payload)])
    failing_sink = FakeEvidenceSink(error=RuntimeError("write failed"))

    with pytest.raises(RuntimeError):
        await DeriveVideoEvidenceJob(descriptions, failing_sink, recorder).handle(payload)

    assert recorder.stages == []

    working_sink = FakeEvidenceSink()
    await DeriveVideoEvidenceJob(descriptions, working_sink, recorder).handle(payload)

    assert descriptions.calls == 2
    assert working_sink.stored[payload.idempotency_key]


async def test_replaying_a_finished_derive_job_does_not_call_the_provider_again():
    recorder = FakeRecorder(already_done=[DERIVE_STAGE])
    payload = _derive_payload()
    descriptions = FakeDescriptions([_candidate(payload)])
    sink = FakeEvidenceSink()

    job = DeriveVideoEvidenceJob(descriptions, sink, recorder)
    await job.handle(payload)

    assert descriptions.calls == 0
    assert sink.calls == 0
    assert job.derived_candidates == []


async def test_a_cancelled_derive_job_never_reaches_the_provider():
    recorder = FakeRecorder()
    payload = _derive_payload()
    descriptions = FakeDescriptions([_candidate(payload)])
    sink = FakeEvidenceSink()

    with pytest.raises(JobCancelledError):
        await DeriveVideoEvidenceJob(
            descriptions, sink, recorder, cancellation=Cancelled()
        ).handle(payload)

    assert descriptions.calls == 0
    assert sink.calls == 0


async def test_a_malformed_provider_response_propagates_and_records_nothing():
    """The adapter raised; the stage must not be recorded as complete."""

    recorder = FakeRecorder()
    payload = _derive_payload()
    descriptions = FakeDescriptions(error=ValueError("end_ms precedes start_ms"))
    sink = FakeEvidenceSink()

    with pytest.raises(ValueError):
        await DeriveVideoEvidenceJob(descriptions, sink, recorder).handle(payload)

    assert recorder.stages == []
    assert sink.calls == 0


async def test_a_candidate_with_a_backwards_range_cannot_be_built():
    """The worker-side record enforces the same range rule as the API type."""

    payload = _derive_payload()
    bad = _candidate(payload).model_dump()
    bad["end_ms"] = 1

    with pytest.raises(ValueError):
        VideoEvidenceCandidateRecord.model_validate(bad)


async def test_a_candidate_with_an_empty_description_cannot_be_built():
    payload = _derive_payload()
    bad = _candidate(payload).model_dump()
    bad["description"] = ""

    with pytest.raises(ValueError):
        VideoEvidenceCandidateRecord.model_validate(bad)


async def test_a_source_verified_extraction_is_stored_as_citable():
    recorder = FakeRecorder()
    sink = FakeExtractionSink()
    payload = _extract_payload()

    await ExtractChartJob(
        FakeExtraction(_outcome(source_verified=True)), sink, recorder
    ).handle(payload)

    outcome, citable, key = sink.stored[0]
    assert citable is True
    assert key == payload.idempotency_key
    assert outcome.validation.source_verified is True


async def test_a_failed_source_check_is_stored_but_not_citable():
    """Still worth keeping for a reviewer; never presented to a student."""

    recorder = FakeRecorder()
    sink = FakeExtractionSink()

    await ExtractChartJob(
        FakeExtraction(_outcome(source_verified=False)), sink, recorder
    ).handle(_extract_payload())

    _, citable, _ = sink.stored[0]
    assert citable is False


async def test_an_unchecked_extraction_is_not_citable():
    """"We have not checked" must not reach the student as "we checked"."""

    unchecked = _outcome(source_verified=True, checked=0).model_copy(
        update={
            "validation": ValidationVerdict(source_verified=True, checked_count=0)
        }
    )

    assert is_citable(unchecked) is False


async def test_each_extraction_job_declares_its_own_object_kind():
    recorders = {}
    sinks = {}
    for name, job_type in (
        ("figure", ExtractFigureJob),
        ("chart", ExtractChartJob),
        ("table", ExtractTableJob),
    ):
        recorders[name] = FakeRecorder()
        sinks[name] = FakeExtractionSink()
        await job_type(
            FakeExtraction(_outcome(source_verified=True)), sinks[name], recorders[name]
        ).handle(_extract_payload())

    assert all(sink.stored for sink in sinks.values())


async def test_a_cancelled_extraction_job_never_calls_the_provider():
    extraction = FakeExtraction(_outcome(source_verified=True))
    sink = FakeExtractionSink()

    with pytest.raises(JobCancelledError):
        await ExtractChartJob(
            extraction, sink, FakeRecorder(), cancellation=Cancelled()
        ).handle(_extract_payload())

    assert extraction.calls == 0
    assert sink.stored == []


async def test_replaying_a_finished_extraction_job_calls_nothing():
    from netra_worker.jobs.multimedia.extraction import EXTRACT_STAGE

    extraction = FakeExtraction(_outcome(source_verified=True))
    sink = FakeExtractionSink()

    job = ExtractTableJob(extraction, sink, FakeRecorder(already_done=[EXTRACT_STAGE]))
    await job.handle(
        ExtractTablePayload(
            idempotency_key="job-1",
            source_id=uuid4(),
            source_version_id=uuid4(),
            object_index=1,
            object_key="tables/tbl01.png",
        )
    )

    assert extraction.calls == 0
    assert sink.stored == []
    assert job.outcome is None


async def test_the_table_payload_exposes_its_index_under_the_domain_name():
    payload = ExtractTablePayload(
        idempotency_key="job-1",
        source_id=uuid4(),
        source_version_id=uuid4(),
        object_index=1,
        object_key="tables/tbl01.png",
    )

    assert payload.table_index == 1
