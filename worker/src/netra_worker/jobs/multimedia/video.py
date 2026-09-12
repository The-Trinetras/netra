"""Multimedia jobs: index a source video with Twelve Labs and derive evidence.

Two stages mirror the Marengo/Pegasus provider split (see
netra_api.multimedia.providers.twelve_labs): IndexVideoJob registers a
video with the provider for later embedding/generation calls, and
DeriveVideoEvidenceJob calls Pegasus/Marengo to produce candidate
VideoEvidenceItem records. Neither job resolves its own output as
citable evidence — netra_api.multimedia.evidence.resolve_and_authorize,
called from netra_api.multimedia.video.service, does that (CLAUDE.md
"Agents never own database connections"; jobs write derived
candidates, the API service layer authorizes them for citation). Must
not hold a PostgreSQL transaction open while awaiting a Twelve Labs
call (CLAUDE.md "Background jobs").
"""

from __future__ import annotations

from netra_worker.jobs.multimedia.base import MultimediaJobPayload


class IndexVideoPayload(MultimediaJobPayload):
    object_key: str
    content_type: str


class IndexVideoJob:
    """Structurally implements netra_worker.runtime.job_repository.JobHandler[IndexVideoPayload].

    TODO: inject a Marengo-shaped provider (see
    netra_api.multimedia.providers.twelve_labs.MarengoProvider) once
    provider wiring for worker/ is decided. No Twelve Labs SDK call is
    implemented here.
    """

    async def handle(self, payload: IndexVideoPayload) -> None:
        raise NotImplementedError("TODO: index_video — no Twelve Labs indexing call implemented")


class DeriveVideoEvidencePayload(MultimediaJobPayload):
    provider_video_id: str


class DeriveVideoEvidenceJob:
    """Structurally implements netra_worker.runtime.job_repository.JobHandler[DeriveVideoEvidencePayload].

    TODO: inject a Pegasus-shaped provider (see
    netra_api.multimedia.providers.twelve_labs.PegasusProvider) and a
    video-evidence persistence store once provider wiring for worker/ is
    decided. No Twelve Labs SDK call is implemented here.
    """

    async def handle(self, payload: DeriveVideoEvidencePayload) -> None:
        raise NotImplementedError("TODO: derive_video_evidence — no Pegasus call implemented")
