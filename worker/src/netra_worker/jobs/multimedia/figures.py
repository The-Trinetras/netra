"""Multimedia job: extract a structured figure or chart description.

Produces a candidate description only — netra_api.multimedia.figures.service
registers it as citable DERIVED evidence, not this job (CLAUDE.md
"Agents never own database connections"). The staging, idempotency,
cancellation and publish-refusal rules all live in
netra_worker.jobs.multimedia.extraction; this module only fixes which
kind of object each job extracts and keeps the historical payload names
that other modules and tests refer to.

A chart is a figure that was decomposed further (see
netra_api.multimedia.figures.charts), so it gets its own job rather
than a flag: a page figure and a plotted graph are validated against
different reviewer records, and the AgentSpec's acceptance case turns
entirely on the chart one.
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


class ExtractFigurePayload(ExtractObjectPayload):
    """One figure within a source version.

    figure_index is the stable ordinal used by
    netra_api.multimedia.figures.evidence.FigureEvidenceReference, so a
    description can be re-found after re-processing.
    """

    @property
    def figure_index(self) -> int:
        return self.object_index


class ExtractFigureJob(ExtractObjectJob):
    """Structurally implements JobHandler[ExtractFigurePayload]."""

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
            kind=ExtractedObjectKind.FIGURE,
            cancellation=cancellation,
            deadline=deadline,
            stage_timeout_seconds=stage_timeout_seconds,
        )


class ExtractChartPayload(ExtractObjectPayload):
    """One plotted graph within a source version."""


class ExtractChartJob(ExtractObjectJob):
    """Structurally implements JobHandler[ExtractChartPayload].

    Produces the axes, units and plotted values that let Netra answer a
    question about what a line shows — the evidence the AgentSpec's
    transcript cannot supply.
    """

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
            kind=ExtractedObjectKind.CHART,
            cancellation=cancellation,
            deadline=deadline,
            stage_timeout_seconds=stage_timeout_seconds,
        )
