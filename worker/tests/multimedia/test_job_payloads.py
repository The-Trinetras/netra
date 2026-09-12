from uuid import uuid4

import pytest

from netra_worker.jobs.multimedia.diagrams import (
    ExtractDiagramStructureJob,
    ExtractDiagramStructurePayload,
)
from netra_worker.jobs.multimedia.equations import ExtractEquationJob, ExtractEquationPayload
from netra_worker.jobs.multimedia.figures import ExtractFigureJob, ExtractFigurePayload
from netra_worker.jobs.multimedia.video import (
    DeriveVideoEvidenceJob,
    DeriveVideoEvidencePayload,
    IndexVideoJob,
    IndexVideoPayload,
)


def _multimedia_kwargs(**extra):
    return dict(idempotency_key="job-1", source_id=uuid4(), source_version_id=uuid4(), **extra)


async def test_extract_figure_job_is_not_yet_implemented():
    payload = ExtractFigurePayload(**_multimedia_kwargs(figure_index=0, object_key="figures/1.png"))
    with pytest.raises(NotImplementedError):
        await ExtractFigureJob().handle(payload)


async def test_extract_diagram_structure_job_is_not_yet_implemented():
    payload = ExtractDiagramStructurePayload(
        **_multimedia_kwargs(figure_index=0, object_key="figures/1.png")
    )
    with pytest.raises(NotImplementedError):
        await ExtractDiagramStructureJob().handle(payload)


async def test_extract_equation_job_is_not_yet_implemented():
    payload = ExtractEquationPayload(**_multimedia_kwargs(equation_index=0, object_key="equations/1.png"))
    with pytest.raises(NotImplementedError):
        await ExtractEquationJob().handle(payload)


async def test_index_video_job_is_not_yet_implemented():
    payload = IndexVideoPayload(**_multimedia_kwargs(object_key="video/1.mp4", content_type="video/mp4"))
    with pytest.raises(NotImplementedError):
        await IndexVideoJob().handle(payload)


async def test_derive_video_evidence_job_is_not_yet_implemented():
    payload = DeriveVideoEvidencePayload(**_multimedia_kwargs(provider_video_id="tlv_abc123"))
    with pytest.raises(NotImplementedError):
        await DeriveVideoEvidenceJob().handle(payload)
