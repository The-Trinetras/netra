"""C7 draft conformance; all data/provider replies are synthetic."""

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from netra_api.multimedia.discovery.models import DiscoveredVideo, DiscoveryQuery, DiscoveryResultSet
from netra_api.multimedia.discovery.wire import (
    YouTubeResult, YouTubeSearchRequest, YouTubeSearchResponse,
    YouTubeSelectionRequest, YouTubeSelectionResponse, public_results,
)

CONTRACTS = Path(__file__).resolve().parents[3] / "shared/contracts/discovery/v1"
MODELS = {
    "search_request": YouTubeSearchRequest,
    "search_response": YouTubeSearchResponse,
    "selection_request": YouTubeSelectionRequest,
    "selection_response": YouTubeSelectionResponse,
}


def response():
    return public_results(DiscoveryResultSet(
        result_set_id=UUID(int=7), query=DiscoveryQuery(query_text="Ohm"),
        produced_at=datetime(2026, 9, 20, tzinfo=timezone.utc),
        results=[DiscoveredVideo(result_ordinal=i, youtube_video_id=letter * 11,
                                 title=f"Lecture {i}", url="https://example.test/ignored")
                 for i, letter in enumerate("AB", 1)],
    ), request_id=UUID(int=8), session_version=3)


@pytest.mark.parametrize("name, model", MODELS.items())
def test_committed_schema_matches_executable_payload(name, model):
    schema = json.loads((CONTRACTS / f"{name}.schema.json").read_text())
    for metadata in ("$schema", "$id", "$comment"):
        schema.pop(metadata)
    assert schema == model.model_json_schema()


@pytest.mark.parametrize("name, model", MODELS.items())
def test_review_examples_parse_without_coercion(name, model):
    example = (CONTRACTS / "examples" / f"{name}.json").read_text()
    assert model.model_validate_json(example).model_dump(mode="json") == json.loads(example)


def test_projection_preserves_list_and_unknown_facts_without_urls_or_snippets():
    actual = response()
    assert actual.result_set_id == UUID(int=7)
    assert actual.request_id == UUID(int=8) and actual.session_version == 3
    assert [(r.result_ordinal, r.youtube_video_id, r.title) for r in actual.results] == [
        (1, "AAAAAAAAAAA", "Lecture 1"), (2, "BBBBBBBBBBB", "Lecture 2")]
    assert all(r.channel is None and r.duration_ms is None and r.embeddable is None for r in actual.results)
    assert "url" not in actual.model_dump_json() and "ignored" not in actual.model_dump_json()
    assert YouTubeSearchResponse.model_validate_json(actual.model_dump_json()) == actual


@pytest.mark.parametrize("mutation", ["reorder", "duplicate_id", "gap", "zero", "extra_field", "bad_id", "boolean_ordinal"])
def test_malformed_result_lists_are_rejected(mutation):
    data = response().model_dump(mode="json")
    rows = data["results"]
    if mutation == "reorder":
        rows.reverse()
    elif mutation == "duplicate_id":
        rows[1]["youtube_video_id"] = rows[0]["youtube_video_id"]
    elif mutation == "gap":
        rows[1]["result_ordinal"] = 3
    elif mutation == "zero":
        rows[0]["result_ordinal"] = 0
    elif mutation == "extra_field":
        rows[0]["snippet"] = "untrusted provider text"
    elif mutation == "bad_id":
        rows[0]["youtube_video_id"] = "https://example.test"
    else:
        rows[0]["result_ordinal"] = True
    with pytest.raises(ValidationError):
        YouTubeSearchResponse.model_validate_json(json.dumps(data))


@pytest.mark.parametrize("field, value", [
    ("max_results", 0), ("max_results", 21), ("max_results", True),
    ("expected_session_version", -1), ("query_text", "  \n\t"),
    ("query_text", "x" * 501), ("api_key", "not-allowed"),
])
def test_search_refuses_invalid_or_private_fields(field, value):
    data = dict(request_id=str(UUID(int=1)), expected_session_version=0, query_text="ohm")
    data[field] = value
    with pytest.raises(ValidationError):
        YouTubeSearchRequest.model_validate_json(json.dumps(data))


def test_query_is_preserved_and_selection_cannot_override_video_identity():
    request = YouTubeSearchRequest(request_id=UUID(int=1), expected_session_version=0,
                                   query_text="  Ohm's Law?  ")
    assert request.to_query().query_text == "  Ohm's Law?  "
    assert request.to_query().max_results == 5
    selection = dict(request_id=str(UUID(int=2)), expected_session_version=3,
                     result_set_id=str(UUID(int=7)), result_ordinal=2)
    parsed = YouTubeSelectionRequest.model_validate_json(json.dumps(selection))
    assert parsed.result_set_id == UUID(int=7) and parsed.result_ordinal == 2
    selection["youtube_video_id"] = "CCCCCCCCCCC"
    with pytest.raises(ValidationError):
        YouTubeSelectionRequest.model_validate_json(json.dumps(selection))


def test_empty_results_are_success_and_unknown_embeddability_is_not_true():
    data = response().model_dump(mode="json")
    data["results"] = []
    assert YouTubeSearchResponse.model_validate_json(json.dumps(data)).results == []
    with pytest.raises(ValidationError):
        YouTubeResult(result_ordinal=1, youtube_video_id="AAAAAAAAAAA", title="a", embeddable="true")


def test_selection_response_retains_exact_selected_metadata():
    offered = response()
    selected = YouTubeSelectionResponse(request_id=UUID(int=9), session_version=4,
        result_set_id=offered.result_set_id, selected_at=offered.produced_at,
        selection=offered.results[1])
    assert YouTubeSelectionResponse.model_validate_json(selected.model_dump_json()).selection == offered.results[1]


def test_responses_require_timezone_and_bound_the_list():
    data = response().model_dump(mode="json")
    data["produced_at"] = "2026-09-20T12:00:00"
    with pytest.raises(ValidationError):
        YouTubeSearchResponse.model_validate_json(json.dumps(data))
    data = response().model_dump(mode="json")
    data["results"] *= 11
    with pytest.raises(ValidationError):
        YouTubeSearchResponse.model_validate_json(json.dumps(data))
