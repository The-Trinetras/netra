from uuid import uuid4

import pytest
from pydantic import ValidationError

from netra_api.multimedia.providers.twelve_labs import (
    MarengoEmbedding,
    MarengoEmbeddingScope,
    PegasusGenerationKind,
    PegasusGenerationRequest,
    PegasusGenerationResult,
    TwelveLabsVideoRef,
)


def _video_ref():
    return TwelveLabsVideoRef(source_version_id=uuid4(), provider_video_id="tlv_abc123")


def test_marengo_embedding_defaults_to_no_time_range():
    embedding = MarengoEmbedding(scope=MarengoEmbeddingScope.TEXT, vector=[0.1, 0.2, 0.3])
    assert embedding.start_ms is None
    assert embedding.end_ms is None


def test_pegasus_generation_request_carries_optional_prompt():
    request = PegasusGenerationRequest(
        video=_video_ref(), kind=PegasusGenerationKind.OPEN_ENDED_QA, prompt="What happens at the end?"
    )
    assert request.prompt == "What happens at the end?"


def test_pegasus_generation_result_is_not_evidence_by_itself():
    result = PegasusGenerationResult(text="A narrator explains photosynthesis.", start_ms=0, end_ms=8000)
    assert not hasattr(result, "evidence_id")


def test_twelve_labs_video_ref_requires_provider_video_id():
    with pytest.raises(ValidationError):
        TwelveLabsVideoRef(source_version_id=uuid4())
