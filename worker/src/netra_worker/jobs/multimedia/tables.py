"""Multimedia job: extract one table's headers, spans and cell values.

Ownership runs through this file, so it is worth stating plainly: M3
extracts and validates the table, M2 stores it and owns its source
version identity (multimedia.md: "M3 validates table extraction; M2 owns
source/table storage and version identity"). This job therefore hands a
reviewed candidate to the sink and stops. It does not write M2's
content records, does not activate a source version, and does not
register evidence.

Produces a candidate structure only — the structure and its validator
are netra_api.multimedia.tables.models.TableStructure and
netra_api.multimedia.tables.validation.validate_table_against_source.
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


class ExtractTablePayload(ExtractObjectPayload):
    """One table within a source version."""

    @property
    def table_index(self) -> int:
        return self.object_index


class ExtractTableJob(ExtractObjectJob):
    """Structurally implements JobHandler[ExtractTablePayload]."""

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
            kind=ExtractedObjectKind.TABLE,
            cancellation=cancellation,
            deadline=deadline,
            stage_timeout_seconds=stage_timeout_seconds,
        )
