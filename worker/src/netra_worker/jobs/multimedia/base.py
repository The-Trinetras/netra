"""Shared contract for multimedia background-job payloads.

Figure/diagram/equation/video processing are explicitly-listed NON-agent
bounded workflows (CLAUDE.md "Architecture: only two agents"). Each
stage below is a netra_worker.runtime.job_repository.JobHandler:
idempotent, and safe to re-run under at-least-once execution (CLAUDE.md
"Background jobs"). This module only fixes the payload fields every
multimedia job shares.
"""

from __future__ import annotations

from uuid import UUID

from netra_worker.runtime.job_repository import JobPayload


class MultimediaJobPayload(JobPayload):
    """Fields every multimedia processing job payload carries.

    source_id/source_version_id mirror
    netra_worker.jobs.ingestion.base.IngestionJobPayload — multimedia
    jobs only ever run against an already-ingested, specific
    SourceVersion, never a bare Source (CLAUDE.md "do not bypass
    source-version checks").
    """

    source_id: UUID
    source_version_id: UUID
