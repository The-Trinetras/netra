"""Multimedia job: extract a layered diagram structure for one figure.

Runs after (or alongside) figure extraction when a figure is classified
as a diagram; produces the layered node/edge/reading-order structure
defined in netra_api.multimedia.diagrams.models. Produces a candidate
structure only — netra_api.multimedia.diagrams.service registers it as
citable DERIVED evidence, not this job.

Staging, idempotency, cancellation and the refusal to publish an
unchecked extraction come from netra_worker.jobs.multimedia.extraction.
"""

from __future__ import annotations

from typing import Optional

from netra_worker.jobs.multimedia.base import CancellationToken, Deadline, StageRecorder
from netra_worker.jobs.multimedia.extraction import (
    ExtractedObjectKind,
    ExtractionCandidateSink,
    ExtractObjectJob,
    ExtractObjectPayload,
    ObjectExtractionPort,
)


class ExtractDiagramStructurePayload(ExtractObjectPayload):
    """One diagram within a source version."""

    @property
    def figure_index(self) -> int:
        return self.object_index


class ExtractDiagramStructureJob(ExtractObjectJob):
    """Structurally implements JobHandler[ExtractDiagramStructurePayload]."""

    def __init__(
        self,
        extraction: ObjectExtractionPort,
        sink: ExtractionCandidateSink,
        recorder: StageRecorder,
        *,
        cancellation: Optional[CancellationToken] = None,
        deadline: Optional[Deadline] = None,
        stage_timeout_seconds: float = 90.0,
    ) -> None:
        super().__init__(
            extraction,
            sink,
            recorder,
            kind=ExtractedObjectKind.DIAGRAM,
            cancellation=cancellation,
            deadline=deadline,
            stage_timeout_seconds=stage_timeout_seconds,
        )
