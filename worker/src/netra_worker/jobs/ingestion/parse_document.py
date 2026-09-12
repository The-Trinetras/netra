"""Ingestion stage: parse an uploaded source into raw structural blocks.

Delegates to a document-parser provider adapter (LlamaParse or another,
behind an interface shaped like
netra_api.content.providers.llamaparse.DocumentParserProvider — not
imported directly since worker/ and api/ are separate deployables;
concrete provider wiring is decided where both are assembled). Actual
parsing is out of scope for this scaffold.
"""

from __future__ import annotations

from netra_worker.jobs.ingestion.base import IngestionJobPayload


class ParseDocumentPayload(IngestionJobPayload):
    object_key: str
    content_type: str


class ParseDocumentJob:
    """Structurally implements netra_worker.runtime.job_repository.JobHandler[ParseDocumentPayload].

    TODO: inject a DocumentParserProvider + ObjectStorageProvider once
    provider wiring for worker/ is decided; must not hold a PostgreSQL
    transaction open while awaiting the parser call (CLAUDE.md
    "Background jobs").
    """

    async def handle(self, payload: ParseDocumentPayload) -> None:
        raise NotImplementedError("TODO: parse_document — no parser provider call implemented")
