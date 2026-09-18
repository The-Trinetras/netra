"""Adapter-boundary validation of Twelve Labs responses.

No SDK, no network, no credentials. These tests exercise the conversion
and rejection rules the adapter applies to provider output, which is
where malformed responses are supposed to stop.

An SDK being pinned in the runtime baseline and these tests passing do
not establish that a Twelve Labs account exists or that Marengo/Pegasus
return anything at all. That requires a separately authorized live
check; see docs/team/handoffs/M3.md.
"""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from fixtures.ohm_law import lecture_asset
from netra_api.multimedia.providers.errors import (
    MalformedProviderResponseError,
    ProviderCancelledError,
    ProviderQuotaExceededError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from netra_api.multimedia.providers.twelve_labs import (
    MarengoEmbedding,
    MarengoEmbeddingScope,
    PegasusGenerationKind,
    PegasusGenerationResult,
)
from netra_api.multimedia.providers.twelve_labs_responses import (
    TWELVE_LABS_PROVIDER,
    pegasus_result_to_candidate,
    provider_video_id_for,
    validate_marengo_embeddings,
    validate_pegasus_result,
)
from netra_api.multimedia.video.models import VideoEvidenceKind

PRODUCED_AT = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)


def _scene(text="A line rises across labelled axes.", start=44_000, end=52_000):
    return PegasusGenerationResult(text=text, start_ms=start, end_ms=end)


def _candidate(result, kind=PegasusGenerationKind.SCENE_DESCRIPTION):
    return pegasus_result_to_candidate(
        result,
        asset=lecture_asset(),
        kind=kind,
        locator="lecture-v1",
        model_name="pegasus",
        model_version="test-fixture",
        produced_at=PRODUCED_AT,
        stage="derive_video_evidence",
    )


def test_a_valid_scene_description_becomes_a_candidate():
    candidate = _candidate(_scene())

    assert candidate.kind is VideoEvidenceKind.VISUAL_DESCRIPTION
    assert (candidate.start_ms, candidate.end_ms) == (44_000, 52_000)
    assert candidate.video_id == lecture_asset().video_id


def test_a_candidate_carries_the_configured_model_identity():
    """The pin Netra agreed to use, not a name echoed by the response."""

    candidate = _candidate(_scene())

    assert candidate.provenance.provider == TWELVE_LABS_PROVIDER
    assert (candidate.provenance.model_name, candidate.provenance.model_version) == (
        "pegasus",
        "test-fixture",
    )
    assert candidate.provenance.stage == "derive_video_evidence"


def test_a_candidate_is_not_citable_evidence():
    """There is no evidence_id to fill in; registration happens elsewhere."""

    candidate = _candidate(_scene())

    assert not hasattr(candidate, "evidence_id")


def test_an_empty_description_is_rejected():
    with pytest.raises(MalformedProviderResponseError) as excinfo:
        validate_pegasus_result(
            _scene(text="   "), kind=PegasusGenerationKind.SCENE_DESCRIPTION
        )

    assert excinfo.value.field == "text"


def test_a_backwards_time_range_is_rejected():
    """The citation is the timestamp, so a wrong one cannot be published."""

    with pytest.raises(MalformedProviderResponseError) as excinfo:
        validate_pegasus_result(
            _scene(start=52_000, end=44_000),
            kind=PegasusGenerationKind.SCENE_DESCRIPTION,
        )

    assert excinfo.value.field == "end_ms"


def test_a_range_past_the_end_of_the_media_is_rejected():
    with pytest.raises(MalformedProviderResponseError):
        validate_pegasus_result(
            _scene(start=80_000, end=200_000),
            kind=PegasusGenerationKind.SCENE_DESCRIPTION,
            duration_ms=lecture_asset().duration_ms,
        )


def test_an_unknown_duration_does_not_reject_every_response():
    """Unknown is not zero: Netra may simply not have measured the media."""

    validate_pegasus_result(
        _scene(start=80_000, end=200_000),
        kind=PegasusGenerationKind.SCENE_DESCRIPTION,
        duration_ms=None,
    )


def test_a_description_without_a_time_range_is_rejected():
    with pytest.raises(MalformedProviderResponseError):
        validate_pegasus_result(
            PegasusGenerationResult(text="Something happens."),
            kind=PegasusGenerationKind.SCENE_DESCRIPTION,
        )


def test_an_overlong_description_is_rejected():
    with pytest.raises(MalformedProviderResponseError):
        validate_pegasus_result(
            _scene(text="x" * 9_000), kind=PegasusGenerationKind.SCENE_DESCRIPTION
        )


def test_question_answers_are_valid_but_never_stored_as_evidence():
    """An answer is shaped by its prompt; it does not describe a moment."""

    answer = PegasusGenerationResult(text="The resistance is two ohms.")
    validate_pegasus_result(answer, kind=PegasusGenerationKind.OPEN_ENDED_QA)

    with pytest.raises(MalformedProviderResponseError) as excinfo:
        _candidate(answer, kind=PegasusGenerationKind.OPEN_ENDED_QA)

    assert excinfo.value.field == "kind"


def test_a_summary_becomes_a_scene_summary_not_a_visual_description():
    candidate = _candidate(_scene(), kind=PegasusGenerationKind.SUMMARY)

    assert candidate.kind is VideoEvidenceKind.SCENE_SUMMARY
    assert candidate.kind.supports_visual_claim is False


def test_conversion_validates_even_when_the_caller_forgot_to():
    with pytest.raises(MalformedProviderResponseError):
        _candidate(_scene(start=52_000, end=44_000))


def test_embeddings_must_match_the_pinned_dimension():
    with pytest.raises(MalformedProviderResponseError) as excinfo:
        validate_marengo_embeddings(
            [MarengoEmbedding(scope=MarengoEmbeddingScope.VIDEO, vector=[0.1, 0.2])],
            expected_dimensions=3,
        )

    assert excinfo.value.field == "vector"


def test_an_empty_embedding_response_is_rejected():
    with pytest.raises(MalformedProviderResponseError):
        validate_marengo_embeddings([], expected_dimensions=3)


def test_an_embedding_with_half_a_time_range_is_rejected():
    with pytest.raises(MalformedProviderResponseError):
        validate_marengo_embeddings(
            [
                MarengoEmbedding(
                    scope=MarengoEmbeddingScope.VIDEO, vector=[0.1, 0.2, 0.3], start_ms=1000
                )
            ],
            expected_dimensions=3,
        )


def test_a_whole_video_embedding_may_omit_its_time_range():
    validate_marengo_embeddings(
        [MarengoEmbedding(scope=MarengoEmbeddingScope.VIDEO, vector=[0.1, 0.2, 0.3])],
        expected_dimensions=3,
    )


def test_expected_dimensions_is_required_rather_than_inferred():
    with pytest.raises(ValueError):
        validate_marengo_embeddings(
            [MarengoEmbedding(scope=MarengoEmbeddingScope.TEXT, vector=[0.1])],
            expected_dimensions=0,
        )


def test_the_canonical_id_is_never_the_provider_id():
    asset = lecture_asset()

    assert provider_video_id_for(asset, "tlv_abc123") == asset.video_id


def test_an_empty_provider_asset_id_is_rejected():
    with pytest.raises(MalformedProviderResponseError):
        provider_video_id_for(lecture_asset(), "")


def test_retryability_matches_what_a_caller_should_do():
    """backend-data.md: do not retry denied access, invalid input or quota."""

    assert ProviderUnavailableError("twelvelabs", "503").retryable is True
    assert ProviderTimeoutError("twelvelabs", "deadline").retryable is True
    assert ProviderQuotaExceededError("twelvelabs", "quota").retryable is False
    assert MalformedProviderResponseError("twelvelabs", "bad").retryable is False
    assert ProviderCancelledError("twelvelabs", "stopped").retryable is False


def test_a_timeout_can_carry_the_operation_it_left_uncertain():
    error = ProviderTimeoutError("twelvelabs", "deadline", operation_id="op-1")

    assert error.operation_id == "op-1"


def test_provider_errors_do_not_leak_sdk_types():
    """A caller catches Netra errors, not a vendor exception hierarchy."""

    error = ProviderUnavailableError("twelvelabs", "503")

    assert type(error).__module__.startswith("netra_api.")
    assert error.provider == "twelvelabs"


def test_a_candidate_for_an_unmeasured_asset_still_validates():
    asset = lecture_asset().model_copy(update={"duration_ms": None})

    candidate = pegasus_result_to_candidate(
        _scene(),
        asset=asset,
        kind=PegasusGenerationKind.SCENE_DESCRIPTION,
        locator="lecture-v1",
        model_name="pegasus",
        model_version="test-fixture",
        produced_at=PRODUCED_AT,
        stage="derive_video_evidence",
    )

    assert candidate.source_version_id == asset.source_version_id


def test_a_candidate_never_borrows_another_assets_identity():
    asset = lecture_asset().model_copy(update={"video_id": uuid4()})

    candidate = pegasus_result_to_candidate(
        _scene(),
        asset=asset,
        kind=PegasusGenerationKind.SCENE_DESCRIPTION,
        locator="lecture-v1",
        model_name="pegasus",
        model_version="test-fixture",
        produced_at=PRODUCED_AT,
        stage="derive_video_evidence",
    )

    assert candidate.video_id == asset.video_id
