"""Shared machinery for document-object extraction jobs.

Figures, diagrams, equations and tables all run the same shape of work:
fetch one object's bytes from storage, ask an extraction provider to
read it, check the result against what a reviewer recorded from the
original media, and hand a candidate to the store — marked with whether
that check actually passed.

The last part is the one worth writing once. multimedia.md: "Do not
publish an incomplete or failed asset as validated evidence" and
"Unverified extraction must not become a confident authoritative
derivation." A job that stores a structure and lets the store decide has
already lost that guarantee, because by then nobody remembers whether
anyone looked at the original. So ExtractionOutcome carries the verdict
with the structure, always, and the sink receives both.

The extracted structures themselves are modelled in
netra_api.multimedia (ChartStructure, DiagramStructure, EquationTree,
TableStructure) and validated by the validators beside them. The worker
imports nothing from netra_api at runtime, so here a structure is an
already-serialized mapping and the verdict is a small record the
extraction adapter produces by running those validators on the API
side. This job owns the ordering, the idempotency and the refusal to
publish — not the extraction semantics.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional, Protocol
from uuid import UUID

from pydantic import BaseModel, Field

from netra_worker.jobs.multimedia.base import (
    CancellationToken,
    Deadline,
    MultimediaJobPayload,
    StageRecorder,
    coerce_record,
    run_stages,
)

EXTRACT_STAGE = "extract_object"


class ExtractedObjectKind(str, Enum):
    """Which multimedia structure an extraction produced."""

    FIGURE = "figure"
    CHART = "chart"
    DIAGRAM = "diagram"
    EQUATION = "equation"
    TABLE = "table"


class ValidationVerdict(BaseModel):
    """The outcome of checking one extraction against the original media.

    Mirrors what netra_api.multimedia.validation.ValidationReport
    computes. Counts rather than the findings themselves: the job needs
    to decide whether to publish, and the findings belong with the
    candidate in the store, where a reviewer can read them.
    """

    source_verified: bool = False
    checked_count: int = Field(default=0, ge=0)
    mismatch_count: int = Field(default=0, ge=0)
    unreadable_count: int = Field(default=0, ge=0)
    unsupported_count: int = Field(default=0, ge=0)
    summary: Optional[str] = None

    @property
    def was_checked(self) -> bool:
        return self.checked_count > 0


class ExtractionOutcome(BaseModel):
    """One extracted object plus the verdict on whether it matches the source."""

    kind: ExtractedObjectKind
    object_index: int = Field(ge=0)
    structure: Dict[str, Any]
    """The serialized netra_api.multimedia structure. Opaque here."""
    validation: ValidationVerdict
    findings: List[Dict[str, Any]] = Field(default_factory=list)
    """Serialized ValidationFindings, stored so a mismatch can be read by
    a person rather than only counted."""


class ObjectExtractionPort(Protocol):
    """Extracts and validates one document object.

    The implementation calls the configured extraction provider behind
    an adapter, builds the netra_api.multimedia structure, runs the
    matching validator against the reviewer's source record, and returns
    both. It raises rather than returning a structure it could not build.
    """

    async def extract(
        self,
        *,
        object_key: str,
        kind: ExtractedObjectKind,
        object_index: int,
        source_version_id: UUID,
        timeout_seconds: float,
    ) -> ExtractionOutcome:
        ...


class ExtractionCandidateSink(Protocol):
    """Where an extracted candidate is persisted. M2 owns the implementation.

    PROPOSED boundary; recorded in docs/team/handoffs/M3.md.
    """

    async def store_candidate(
        self,
        *,
        source_version_id: UUID,
        outcome: ExtractionOutcome,
        citable: bool,
        idempotency_key: str,
    ) -> None:
        """Persist one candidate. Idempotent by idempotency_key.

        citable is the job's decision, passed explicitly rather than left
        for the store to infer from the verdict. An implementation must
        never mark a candidate citable when this is False, and must never
        register DERIVED evidence itself — that stays with the API
        service layer that can authorize it.
        """
        ...


class ExtractObjectPayload(MultimediaJobPayload):
    """One document object to extract, addressed within its source version."""

    object_index: int = Field(ge=0)
    object_key: str
    """Object-storage key of the source crop or page image, e.g. via a
    store shaped like netra_api.content.providers.s3.ObjectStorageProvider."""


class ExtractObjectJob:
    """Structurally implements JobHandler[ExtractObjectPayload].

    One recorded stage, for the same reason DeriveVideoEvidenceJob has
    one: the extracted structure lives in memory, so a stage that
    completed before the write would resume with nothing to store.
    Extraction is re-run on retry and the sink deduplicates.
    """

    def __init__(
        self,
        extraction: ObjectExtractionPort,
        sink: ExtractionCandidateSink,
        recorder: StageRecorder,
        *,
        kind: ExtractedObjectKind,
        cancellation: Optional[CancellationToken] = None,
        deadline: Optional[Deadline] = None,
        stage_timeout_seconds: float = 90.0,
    ) -> None:
        self._extraction = extraction
        self._sink = sink
        self._recorder = recorder
        self._kind = kind
        self._cancellation = cancellation
        self._deadline = deadline
        self._stage_timeout_seconds = stage_timeout_seconds
        self._outcome: Optional[ExtractionOutcome] = None

    async def handle(self, payload: ExtractObjectPayload) -> None:
        async def extract_stage() -> None:
            raw = await self._extraction.extract(
                object_key=payload.object_key,
                kind=self._kind,
                object_index=payload.object_index,
                source_version_id=payload.source_version_id,
                timeout_seconds=self._timeout(),
            )
            outcome = coerce_record(ExtractionOutcome, raw)
            if outcome.kind is not self._kind or outcome.object_index != payload.object_index:
                raise ValueError(
                    "extraction returned a different object than the one requested"
                )
            self._outcome = outcome
            await self._sink.store_candidate(
                source_version_id=payload.source_version_id,
                outcome=outcome,
                citable=is_citable(outcome),
                idempotency_key=payload.idempotency_key,
            )
            return None

        await run_stages(
            self._recorder,
            [(EXTRACT_STAGE, extract_stage)],
            cancellation=self._cancellation,
            deadline=self._deadline,
        )

    @property
    def outcome(self) -> Optional[ExtractionOutcome]:
        """What the last run extracted. None after a replay that skipped the stage."""

        return self._outcome

    def _timeout(self) -> float:
        if self._deadline is None:
            return self._stage_timeout_seconds
        return max(0.0, min(self._stage_timeout_seconds, self._deadline.remaining_seconds()))


def is_citable(outcome: ExtractionOutcome) -> bool:
    """Whether this extraction may be presented to a student as the document's content.

    Requires an actual source check that passed. An extraction nobody
    compared to the original is stored — it is still useful to a
    reviewer, and re-extracting is expensive — but it is not citable,
    because "we have not checked" and "we checked and it matched" must
    not reach the student as the same thing.
    """

    return outcome.validation.source_verified and outcome.validation.was_checked
