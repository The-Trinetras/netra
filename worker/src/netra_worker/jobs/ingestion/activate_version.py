"""Worker job for transactional source-version activation."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from netra_worker.jobs.ingestion.base import IngestionJobPayload
from netra_api.content.telemetry import instrument_stage


class ActivateVersionPayload(IngestionJobPayload):
    source_id: UUID
    source_version_id: UUID
    expected_version_number: int


class ActivationStore(Protocol):
    async def activate_version_internal(
        self, source_id: UUID, source_version_id: UUID, expected_version_number: int
    ) -> object:
        ...


class ActivateVersionJob:
    """Delegate activation to the canonical PostgreSQL source repository."""

    def __init__(self, sources: ActivationStore) -> None:
        self.sources = sources

    @instrument_stage("activate_version")
    async def handle(self, payload: ActivateVersionPayload) -> None:
        await self.sources.activate_version_internal(
            payload.source_id, payload.source_version_id, payload.expected_version_number
        )
