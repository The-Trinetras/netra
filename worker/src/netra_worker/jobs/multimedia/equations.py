"""Multimedia job: extract a navigable equation tree for one equation.

Produces a candidate tree only — netra_api.multimedia.equations.service
registers it as citable DERIVED evidence, not this job.

The publish refusal in netra_worker.jobs.multimedia.extraction matters
most here. An equation tree that parses is not an equation that was read
correctly (multimedia.md: "Successful syntax conversion does not prove
the source was read correctly"), so a tree whose canonical form was
never compared to the source crop is stored but not citable.
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


class ExtractEquationPayload(ExtractObjectPayload):
    """One equation within a source version."""

    @property
    def equation_index(self) -> int:
        return self.object_index


class ExtractEquationJob(ExtractObjectJob):
    """Structurally implements JobHandler[ExtractEquationPayload]."""

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
            kind=ExtractedObjectKind.EQUATION,
            cancellation=cancellation,
            deadline=deadline,
            stage_timeout_seconds=stage_timeout_seconds,
        )
