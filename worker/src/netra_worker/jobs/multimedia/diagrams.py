"""Multimedia job: extract a layered diagram structure for one figure.

Runs after (or alongside) figure extraction when a figure is classified
as a diagram; produces the layered node/edge/reading-order structure
defined in netra_api.multimedia.diagrams.models. Must not hold a
PostgreSQL transaction open while awaiting the provider call (CLAUDE.md
"Background jobs"). Produces a candidate structure only —
netra_api.multimedia.diagrams.service registers it as citable DERIVED
evidence, not this job (CLAUDE.md "Agents never own database
connections").
"""

from __future__ import annotations

from netra_worker.jobs.multimedia.base import MultimediaJobPayload


class ExtractDiagramStructurePayload(MultimediaJobPayload):
    figure_index: int
    object_key: str


class ExtractDiagramStructureJob:
    """Structurally implements netra_worker.runtime.job_repository.JobHandler[ExtractDiagramStructurePayload].

    TODO: inject a diagram-structure-extraction provider + persistence
    store once provider wiring for worker/ is decided.
    """

    async def handle(self, payload: ExtractDiagramStructurePayload) -> None:
        raise NotImplementedError(
            "TODO: extract_diagram_structure — no diagram extraction provider call implemented"
        )
