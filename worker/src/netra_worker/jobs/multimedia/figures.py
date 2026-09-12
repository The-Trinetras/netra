"""Multimedia job: extract a structured figure description for one figure.

Delegates figure description generation to a vision-capable provider
behind an interface (not decided here — CLAUDE.md "Provider adapters");
persists through a store shaped like a figures repository (not yet
defined; added alongside the figures persistence migration, not here).
Must not hold a PostgreSQL transaction open while awaiting the provider
call (CLAUDE.md "Background jobs"). Produces a candidate description
only — netra_api.multimedia.figures.service registers it as citable
DERIVED evidence, not this job (CLAUDE.md "Agents never own database
connections").
"""

from __future__ import annotations

from netra_worker.jobs.multimedia.base import MultimediaJobPayload


class ExtractFigurePayload(MultimediaJobPayload):
    figure_index: int
    object_key: str
    """Object-storage key of the source figure image, e.g. via a store
    shaped like netra_api.content.providers.s3.ObjectStorageProvider."""


class ExtractFigureJob:
    """Structurally implements netra_worker.runtime.job_repository.JobHandler[ExtractFigurePayload].

    TODO: inject a vision-description provider + persistence store once
    provider wiring for worker/ is decided.
    """

    async def handle(self, payload: ExtractFigurePayload) -> None:
        raise NotImplementedError("TODO: extract_figure — no vision provider call implemented")
