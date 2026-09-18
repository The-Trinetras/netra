"""YouTube discovery: stable numbering, exact selection, cost gate.

"Present numbered results and retain the exact selected result" and
"unclear relevance prompts clarification before expensive processing"
(AgentSpec section 8).
"""

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from netra_api.multimedia.discovery.adapter import number_results
from netra_api.multimedia.discovery.models import (
    DiscoveredVideo,
    DiscoveryQuery,
    DiscoveryResultSet,
    RelevanceAssessment,
)
from netra_api.multimedia.discovery.selection import (
    ProcessingDecision,
    UnknownResultError,
    assess_processing,
    select_by_ordinal,
    select_by_video_id,
)

SELECTED_AT = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)


def _result(ordinal: int, video_id: str, **extra) -> DiscoveredVideo:
    return DiscoveredVideo(
        result_ordinal=ordinal,
        youtube_video_id=video_id,
        title=f"Ohm's law lecture {ordinal}",
        channel="Physics Dept",
        **extra,
    )


def _result_set(*results: DiscoveredVideo) -> DiscoveryResultSet:
    return DiscoveryResultSet(
        result_set_id=uuid4(),
        query=DiscoveryQuery(query_text="ohm's law lecture", learning_goal="ohm's law"),
        results=list(results),
        produced_at=SELECTED_AT,
    )


def test_selecting_the_second_result_returns_that_exact_video():
    result_set = _result_set(_result(1, "aaa"), _result(2, "bbb"), _result(3, "ccc"))

    selection = select_by_ordinal(result_set, 2, SELECTED_AT)

    assert selection.youtube_video_id == "bbb"
    assert selection.result_ordinal == 2
    assert selection.result_set_id == result_set.result_set_id


def test_an_out_of_range_selection_fails_rather_than_clamping():
    """Quietly giving the fifth result to somebody who said "sixth" is worse."""

    result_set = _result_set(_result(1, "aaa"), _result(2, "bbb"))

    with pytest.raises(UnknownResultError) as excinfo:
        select_by_ordinal(result_set, 6, SELECTED_AT)

    assert excinfo.value.available == 2


def test_a_pasted_url_resolves_through_the_offered_list():
    result_set = _result_set(_result(1, "aaa"), _result(2, "bbb"))

    selection = select_by_video_id(result_set, "bbb", SELECTED_AT)

    assert selection.result_ordinal == 2


def test_a_video_that_was_never_offered_cannot_be_selected():
    result_set = _result_set(_result(1, "aaa"))

    with pytest.raises(UnknownResultError):
        select_by_video_id(result_set, "zzz", SELECTED_AT)


def test_result_ordinals_must_be_contiguous_from_one():
    with pytest.raises(ValidationError):
        _result_set(_result(1, "aaa"), _result(3, "ccc"))


def test_result_ordinals_must_start_at_one():
    with pytest.raises(ValidationError):
        _result_set(_result(0, "aaa"))


def test_the_same_video_must_not_appear_twice():
    with pytest.raises(ValidationError):
        _result_set(_result(1, "aaa"), _result(2, "aaa"))


def test_number_results_renumbers_and_drops_duplicates():
    """Adapter helper: raw provider lists arrive unordered and repeat."""

    numbered = number_results(
        [_result(9, "aaa"), _result(4, "bbb"), _result(7, "aaa"), _result(1, "ccc")]
    )

    assert [r.result_ordinal for r in numbered] == [1, 2, 3]
    assert [r.youtube_video_id for r in numbered] == ["aaa", "bbb", "ccc"]


def test_number_results_output_builds_a_valid_result_set():
    numbered = number_results([_result(5, "aaa"), _result(5, "aaa"), _result(2, "bbb")])

    result_set = _result_set(*numbered)

    assert len(result_set.results) == 2


def test_clear_relevance_allows_processing():
    result_set = _result_set(_result(1, "aaa"))
    selection = select_by_ordinal(result_set, 1, SELECTED_AT)

    gate = assess_processing(selection, RelevanceAssessment.CLEAR)

    assert gate.decision is ProcessingDecision.PROCEED


def test_ambiguous_relevance_asks_before_spending_anything():
    result_set = _result_set(_result(1, "aaa"))
    selection = select_by_ordinal(result_set, 1, SELECTED_AT)

    gate = assess_processing(selection, RelevanceAssessment.AMBIGUOUS)

    assert gate.decision is ProcessingDecision.CLARIFY
    assert selection.title in gate.clarification_prompt


def test_unassessed_relevance_behaves_like_ambiguous():
    """Not knowing and not being sure lead to the same safe action."""

    result_set = _result_set(_result(1, "aaa"))
    selection = select_by_ordinal(result_set, 1, SELECTED_AT)

    gate = assess_processing(selection, RelevanceAssessment.NOT_ASSESSED)

    assert gate.decision is ProcessingDecision.CLARIFY


def test_irrelevant_selection_is_rejected():
    result_set = _result_set(_result(1, "aaa"))
    selection = select_by_ordinal(result_set, 1, SELECTED_AT)

    gate = assess_processing(selection, RelevanceAssessment.NOT_RELEVANT)

    assert gate.decision is ProcessingDecision.REJECT


def test_media_the_provider_cannot_ingest_is_rejected_even_when_relevant():
    result_set = _result_set(_result(1, "aaa"))
    selection = select_by_ordinal(result_set, 1, SELECTED_AT)

    gate = assess_processing(selection, RelevanceAssessment.CLEAR, ingestible=False)

    assert gate.decision is ProcessingDecision.REJECT
    assert "cannot ingest" in gate.reason


def test_an_over_long_lecture_is_asked_about_rather_than_refused():
    result_set = _result_set(_result(1, "aaa", duration_ms=7_200_000))
    selection = select_by_ordinal(result_set, 1, SELECTED_AT)

    gate = assess_processing(
        selection, RelevanceAssessment.CLEAR, max_duration_ms=3_600_000
    )

    assert gate.decision is ProcessingDecision.CLARIFY
    assert "longer than" in gate.reason


def test_unknown_ingestibility_does_not_block_processing():
    """Indexing is usually how ingestibility is discovered."""

    result_set = _result_set(_result(1, "aaa"))
    selection = select_by_ordinal(result_set, 1, SELECTED_AT)

    gate = assess_processing(selection, RelevanceAssessment.CLEAR, ingestible=None)

    assert gate.decision is ProcessingDecision.PROCEED


def test_a_query_is_bounded_because_results_are_read_aloud():
    with pytest.raises(ValidationError):
        DiscoveryQuery(query_text="ohm", max_results=50)


def test_embeddability_defaults_to_unknown_not_permitted():
    assert _result(1, "aaa").embeddable is None
