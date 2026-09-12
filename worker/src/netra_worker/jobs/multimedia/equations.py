"""Multimedia job: extract a navigable equation tree for one equation.

Delegates equation-tree extraction to a provider behind an interface
(not decided here); persists through an equations repository (not yet
defined). Must not hold a PostgreSQL transaction open while awaiting
the provider call (CLAUDE.md "Background jobs"). Produces a candidate
tree only — netra_api.multimedia.equations.service registers it as
citable DERIVED evidence, not this job (CLAUDE.md "Agents never own
database connections").
"""

from __future__ import annotations

from netra_worker.jobs.multimedia.base import MultimediaJobPayload


class ExtractEquationPayload(MultimediaJobPayload):
    equation_index: int
    object_key: str


class ExtractEquationJob:
    """Structurally implements netra_worker.runtime.job_repository.JobHandler[ExtractEquationPayload].

    TODO: inject an equation-extraction provider + persistence store
    once provider wiring for worker/ is decided.
    """

    async def handle(self, payload: ExtractEquationPayload) -> None:
        raise NotImplementedError("TODO: extract_equation — no equation provider call implemented")
