"""Validate the real E3 golden dataset against canonical PostgreSQL rows."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from netra_api.config import Settings
from netra_api.db.models import SearchChunkRow, SourceRow, SourceVersionRow
from retrieval_evaluation import GoldenEvaluationCase, load_golden_cases


async def validate_against_database(path: str | Path, session: AsyncSession) -> list[GoldenEvaluationCase]:
    cases = load_golden_cases(path, dataset_id="netra-e3-real-golden-retrieval", dataset_version="1")
    version_ids = {version_id for case in cases for version_id in case.source_version_ids}
    versions = (await session.execute(select(SourceVersionRow).where(
        SourceVersionRow.source_version_id.in_(version_ids)))).scalars().all()
    by_version = {version.source_version_id: version for version in versions}
    if len(by_version) != len(version_ids):
        raise ValueError("golden dataset references a missing source version")
    source_ids = {source_id for case in cases for source_id in case.source_ids}
    sources = (await session.execute(select(SourceRow).where(SourceRow.source_id.in_(source_ids)))).scalars().all()
    if len(sources) != len(source_ids):
        raise ValueError("golden dataset references a missing source")
    chunk_ids = {UUID(chunk_id) for case in cases for chunk_id in case.reference_evidence_ids}
    chunks = (await session.execute(select(SearchChunkRow).where(SearchChunkRow.chunk_id.in_(chunk_ids)))).scalars().all()
    by_chunk = {chunk.chunk_id: chunk for chunk in chunks}
    if len(by_chunk) != len(chunk_ids):
        raise ValueError("golden dataset references a missing search chunk")
    for case in cases:
        if len(case.reference_evidence_ids) != len(set(case.reference_evidence_ids)):
            raise ValueError(f"duplicate reference chunks in {case.example_id}")
        if len(case.source_version_ids) != 1 or len(case.source_ids) != 1:
            raise ValueError(f"case must have one source and version scope: {case.example_id}")
        version = by_version[case.source_version_ids[0]]
        if not version.is_active:
            raise ValueError(f"case references an inactive source version: {case.example_id}")
        if version.source_id != case.source_ids[0]:
            raise ValueError(f"source/version mismatch: {case.example_id}")
        for evidence_id in case.reference_evidence_ids:
            chunk = by_chunk[UUID(evidence_id)]
            if chunk.source_version_id != version.source_version_id:
                raise ValueError(f"chunk/version mismatch: {case.example_id}")
    return cases


__all__ = ["validate_against_database"]
