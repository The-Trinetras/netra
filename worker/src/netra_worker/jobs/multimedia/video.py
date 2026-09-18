"""Multimedia jobs: index a source video with Twelve Labs and derive evidence.

Two stages mirror the Marengo/Pegasus provider split (see
netra_api.multimedia.providers.twelve_labs): IndexVideoJob registers a
video with the provider for later embedding/generation calls, and
DeriveVideoEvidenceJob calls Pegasus to produce candidate evidence
records.

Neither job resolves its own output as citable evidence.
netra_api.multimedia.evidence.resolve_and_authorize, called from
netra_api.multimedia.video.service, does that: jobs write derived
candidates, the API service layer authorizes them for citation. That is
why the sink below takes candidates and not evidence, and why no field
on a candidate could promote it.

Neither job holds a database transaction open across a provider call.
The staging loop in netra_worker.jobs.multimedia.base commits each
stage on its own, immediately after the external effect it describes.

On the duplicated record shape: VideoEvidenceCandidateRecord below
mirrors netra_api.multimedia.video.models.VideoEvidenceCandidate field
for field, because the worker imports nothing from netra_api at runtime
(worker/pyproject.toml). The two are kept in step by hand today. A
shared schema under shared/contracts/jobs/v1/ would remove the drift
risk, which is a contract addition needing M1/M2 review — recorded in
docs/team/handoffs/M3.md, not assumed here.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional, Protocol
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from netra_worker.jobs.multimedia.base import (
    CancellationToken,
    Deadline,
    MultimediaJobPayload,
    StageRecorder,
    coerce_record,
    run_stages,
)

INDEX_STAGE = "index_video"
BIND_STAGE = "bind_provider_asset"
DERIVE_STAGE = "derive_video_evidence"


class VideoEvidenceKindName(str, Enum):
    """Mirrors netra_api.multimedia.video.models.VideoEvidenceKind.

    Only these values cross the worker/API line, so a provider payload
    cannot introduce a fourth kind that nothing downstream understands.
    """

    TRANSCRIPT_SEGMENT = "transcript_segment"
    VISUAL_DESCRIPTION = "visual_description"
    SCENE_SUMMARY = "scene_summary"


class EvidenceProvenanceRecord(BaseModel):
    """Which provider, model and stage produced one candidate."""

    provider: str
    model_name: str
    model_version: str
    produced_at: datetime
    stage: str


class VideoEvidenceCandidateRecord(BaseModel):
    """One derived, not-yet-citable piece of video evidence."""

    video_id: UUID
    source_version_id: UUID
    locator: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    kind: VideoEvidenceKindName
    description: str = Field(min_length=1)
    provenance: EvidenceProvenanceRecord

    @model_validator(mode="after")
    def _check_range(self) -> "VideoEvidenceCandidateRecord":
        if self.end_ms < self.start_ms:
            raise ValueError("end_ms must be >= start_ms")
        return self


class VideoIndexingPort(Protocol):
    """Registers media with the provider so it can be embedded and described.

    Netra-owned in both directions: the implementation adapts the Twelve
    Labs SDK and raises the errors in
    netra_api.multimedia.providers.errors, converted to worker-visible
    exceptions by the adapter it is constructed with. Nothing here sees
    an SDK object.
    """

    async def index(
        self,
        *,
        external_ref: str,
        content_type: str,
        timeout_seconds: float,
    ) -> str:
        """Start or complete indexing; return the provider's asset id."""
        ...

    async def find_existing(self, *, external_ref: str) -> Optional[str]:
        """The provider asset id for already-indexed media, if any.

        Used for reconciliation after an uncertain completion: a timeout
        does not prove the provider did nothing, so a retry asks before
        it indexes again (backend-data.md).
        """
        ...


class VideoDescriptionPort(Protocol):
    """Produces validated candidate evidence for one indexed video."""

    async def describe(
        self,
        *,
        provider_video_id: str,
        video_id: UUID,
        source_version_id: UUID,
        locator: str,
        timeout_seconds: float,
    ) -> List[VideoEvidenceCandidateRecord]:
        """Return candidates, or an empty list when none could be derived.

        An implementation must have validated each provider response
        first (see
        netra_api.multimedia.providers.twelve_labs_responses) and must
        raise rather than return a candidate whose time range it could
        not trust.
        """
        ...


class VideoEvidenceSink(Protocol):
    """Where candidates are persisted. M2 owns the implementation.

    PROPOSED boundary; recorded in docs/team/handoffs/M3.md.
    """

    async def store_candidates(
        self,
        *,
        video_id: UUID,
        candidates: List[VideoEvidenceCandidateRecord],
        idempotency_key: str,
    ) -> None:
        """Persist candidates. Idempotent by idempotency_key.

        Re-running with the same key must leave one copy, not two: the
        derive stage can complete, the worker can die before its stage
        write commits, and the retry will call this again with the same
        key.
        """
        ...


class ProviderBindingSink(Protocol):
    """Records which provider asset backs a canonical video."""

    async def bind(
        self,
        *,
        video_id: UUID,
        provider: str,
        provider_index_id: str,
        provider_video_id: str,
        model_name: str,
        model_version: str,
    ) -> None:
        ...


class IndexVideoPayload(MultimediaJobPayload):
    video_id: UUID
    external_ref: str
    """Object-storage key for an upload, or the YouTube video id. Never a
    provider asset id (multimedia.md: "Keep provider asset/index IDs
    distinct from canonical Netra source IDs")."""
    content_type: str


class IndexVideoJob:
    """Structurally implements JobHandler[IndexVideoPayload].

    Registers one video with the configured provider and records the
    binding. Split into two recorded stages so a crash between the
    provider call and the binding write is recoverable: the asset id is
    committed as the index stage's remote operation id the moment it
    exists.
    """

    def __init__(
        self,
        indexing: VideoIndexingPort,
        bindings: ProviderBindingSink,
        recorder: StageRecorder,
        *,
        provider: str,
        provider_index_id: str,
        model_name: str,
        model_version: str,
        cancellation: Optional[CancellationToken] = None,
        deadline: Optional[Deadline] = None,
        stage_timeout_seconds: float = 60.0,
    ) -> None:
        self._indexing = indexing
        self._bindings = bindings
        self._recorder = recorder
        self._provider = provider
        self._provider_index_id = provider_index_id
        self._model_name = model_name
        self._model_version = model_version
        self._cancellation = cancellation
        self._deadline = deadline
        self._stage_timeout_seconds = stage_timeout_seconds

    async def handle(self, payload: IndexVideoPayload) -> None:
        async def index_stage() -> str:
            # An id recorded by a previous attempt means the media is
            # already indexed, even if that attempt never finished.
            recorded = await self._recorder.remote_operation_id(INDEX_STAGE)
            if recorded:
                return recorded
            existing = await self._indexing.find_existing(external_ref=payload.external_ref)
            if existing:
                return existing
            return await self._indexing.index(
                external_ref=payload.external_ref,
                content_type=payload.content_type,
                timeout_seconds=self._timeout(),
            )

        async def bind_stage() -> None:
            provider_video_id = await self._recorder.remote_operation_id(INDEX_STAGE)
            if not provider_video_id:
                raise ValueError(
                    "index stage completed without recording a provider asset id"
                )
            await self._bindings.bind(
                video_id=payload.video_id,
                provider=self._provider,
                provider_index_id=self._provider_index_id,
                provider_video_id=provider_video_id,
                model_name=self._model_name,
                model_version=self._model_version,
            )
            return None

        await run_stages(
            self._recorder,
            [(INDEX_STAGE, index_stage), (BIND_STAGE, bind_stage)],
            cancellation=self._cancellation,
            deadline=self._deadline,
        )

    def _timeout(self) -> float:
        """Never let a provider call outlive the work it belongs to."""

        if self._deadline is None:
            return self._stage_timeout_seconds
        return max(0.0, min(self._stage_timeout_seconds, self._deadline.remaining_seconds()))


class DeriveVideoEvidencePayload(MultimediaJobPayload):
    video_id: UUID
    provider_video_id: str
    locator: str
    """Identifies this video within its source version, matching the
    locator convention used by evidence references."""


class DeriveVideoEvidenceJob:
    """Structurally implements JobHandler[DeriveVideoEvidencePayload].

    Deriving and persisting are ONE stage, deliberately.

    Splitting them looks better — the derive call spends provider budget
    and the persist call does not, so a retry after a failed write would
    ideally skip it. But a stage is only skipped once it is *recorded*
    complete, and the derived candidates live in memory: a crash between
    a recorded derive and a successful persist would resume at persist
    with nothing to write, and the evidence would be silently lost. The
    cheaper retry is not worth a stage that can complete having stored
    nothing.

    So a retry re-derives, and the sink deduplicates on
    idempotency_key. Provider spend is bounded by the job's attempt
    limit; correctness is not traded for it.
    """

    def __init__(
        self,
        descriptions: VideoDescriptionPort,
        sink: VideoEvidenceSink,
        recorder: StageRecorder,
        *,
        cancellation: Optional[CancellationToken] = None,
        deadline: Optional[Deadline] = None,
        stage_timeout_seconds: float = 120.0,
    ) -> None:
        self._descriptions = descriptions
        self._sink = sink
        self._recorder = recorder
        self._cancellation = cancellation
        self._deadline = deadline
        self._stage_timeout_seconds = stage_timeout_seconds
        self._derived: List[VideoEvidenceCandidateRecord] = []

    async def handle(self, payload: DeriveVideoEvidencePayload) -> None:
        async def derive_and_persist() -> None:
            raw = await self._descriptions.describe(
                provider_video_id=payload.provider_video_id,
                video_id=payload.video_id,
                source_version_id=payload.source_version_id,
                locator=payload.locator,
                timeout_seconds=self._timeout(),
            )
            derived = [coerce_record(VideoEvidenceCandidateRecord, item) for item in raw]
            for candidate in derived:
                if (
                    candidate.video_id != payload.video_id
                    or candidate.source_version_id != payload.source_version_id
                ):
                    raise ValueError(
                        "description returned evidence for a different video or source version"
                    )
            self._derived = derived
            if not self._derived:
                # Nothing derived is a real outcome, not a failure: the
                # video is left without visual evidence, and readiness
                # reports it as transcript-only rather than ready.
                return None
            await self._sink.store_candidates(
                video_id=payload.video_id,
                candidates=self._derived,
                idempotency_key=payload.idempotency_key,
            )
            return None

        await run_stages(
            self._recorder,
            [(DERIVE_STAGE, derive_and_persist)],
            cancellation=self._cancellation,
            deadline=self._deadline,
        )

    @property
    def derived_candidates(self) -> List[VideoEvidenceCandidateRecord]:
        """What the last run derived. Empty after a replay that skipped the stage."""

        return list(self._derived)

    def _timeout(self) -> float:
        if self._deadline is None:
            return self._stage_timeout_seconds
        return max(0.0, min(self._stage_timeout_seconds, self._deadline.remaining_seconds()))
