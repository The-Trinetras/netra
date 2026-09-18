"""Tavily YouTube discovery adapter.

TEST-ONLY client doubles shaped after the tavily-python 0.7.27 README;
no Tavily call is made and account access is not established (M3-SDK-2).
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import pytest

from fixtures.provider_fakes import InvalidAPIKeyError, local_tracer
from netra_api.multimedia.discovery.models import DiscoveryQuery
from netra_api.multimedia.discovery.selection import select_by_ordinal
from netra_api.multimedia.discovery.tavily import (
    YOUTUBE_DOMAINS,
    TavilyDiscoverySettings,
    TavilyYouTubeDiscovery,
    build_result_set,
    clean_title,
    parse_youtube_video_id,
)
from netra_api.multimedia.providers.errors import (
    MalformedProviderResponseError,
    ProviderAccessDeniedError,
    ProviderConfigurationError,
    ProviderTimeoutError,
)

SETTINGS = TavilyDiscoverySettings(search_depth="basic", timeout_seconds=2.0, request_max_results=10)


class FakeTavily:
    """Async, like AsyncTavilyClient."""

    def __init__(self, response=None, error=None, delay=0.0):
        self.response = response
        self.error = error
        self.delay = delay
        self.calls = []

    async def search(self, **kwargs):
        import asyncio

        self.calls.append(kwargs)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.response


def _response():
    return {
        "query": "ohm's law voltage current graph",
        "results": [
            {"title": "Ohm's Law explained", "url": "https://www.youtube.com/watch?v=AAAAAAAAAAA&t=48s&utm_source=x", "content": "SNIPPET-1", "score": 0.9},
            {"title": "Ohm's Law channel", "url": "https://www.youtube.com/@physics", "content": "SNIPPET-2", "score": 0.8},
            {"title": "An article", "url": "https://example.com/ohms-law", "content": "SNIPPET-3", "score": 0.7},
            {"title": "Ohm's\u0007 Law\n  lab", "url": "https://youtu.be/BBBBBBBBBBB?si=track", "content": "SNIPPET-4", "score": 0.6},
            {"title": "Duplicate", "url": "https://m.youtube.com/watch?v=AAAAAAAAAAA", "content": "", "score": 0.5},
            {"title": None, "url": "https://www.youtube.com/shorts/CCCCCCCCCCC", "content": "", "score": 0.4},
            {"title": "Playlist", "url": "https://www.youtube.com/playlist?list=PL123", "content": "", "score": 0.3},
        ],
    }


@pytest.mark.parametrize(
    "url, video_id",
    [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://youtube.com/watch?v=dQw4w9WgXcQ&list=PL1", "dQw4w9WgXcQ"),
        ("https://youtu.be/dQw4w9WgXcQ?t=10", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/embed/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/live/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/@channel", None),
        ("https://www.youtube.com/playlist?list=PL1", None),
        ("https://www.youtube.com/watch?v=short", None),
        ("https://evil.example/watch?v=dQw4w9WgXcQ", None),
        ("https://youtube.com.evil.example/watch?v=dQw4w9WgXcQ", None),
        ("javascript:alert(1)", None),
        (None, None),
    ],
)
def test_only_canonical_youtube_video_urls_parse(url, video_id):
    assert parse_youtube_video_id(url) == video_id


async def test_discovery_is_restricted_to_youtube_and_returns_numbered_videos_only():
    client = FakeTavily(_response())
    adapter = TavilyYouTubeDiscovery(client, SETTINGS)

    results = await adapter.search(DiscoveryQuery(query_text="ohm's law voltage current graph", max_results=5))

    assert client.calls[0]["include_domains"] == list(YOUTUBE_DOMAINS)
    assert client.calls[0]["include_raw_content"] is False
    assert [(r.result_ordinal, r.youtube_video_id) for r in results] == [(1, "AAAAAAAAAAA"), (2, "BBBBBBBBBBB")]
    assert results[0].url == "https://www.youtube.com/watch?v=AAAAAAAAAAA"
    assert results[1].title == "Ohm's Law lab"
    assert all(r.duration_ms is None and r.embeddable is None and r.channel is None for r in results)
    report = adapter.last_report
    assert (report.returned, report.not_a_video, report.untitled, report.duplicates) == (7, 3, 1, 1)
    assert "SNIPPET" not in repr(results)


async def test_results_are_capped_at_the_query_max_and_selection_is_stable():
    adapter = TavilyYouTubeDiscovery(FakeTavily(_response()), SETTINGS)
    query = DiscoveryQuery(query_text="ohm", max_results=1)
    results = await adapter.search(query)
    assert len(results) == 1
    result_set = build_result_set(query, results, id_factory=lambda: UUID(int=7))
    assert select_by_ordinal(result_set, 1, datetime(2026, 9, 18, tzinfo=timezone.utc)).youtube_video_id == "AAAAAAAAAAA"


async def test_malformed_response_and_named_sdk_failures_convert():
    with pytest.raises(MalformedProviderResponseError):
        await TavilyYouTubeDiscovery(FakeTavily({"answer": "x"}), SETTINGS).search(DiscoveryQuery(query_text="q"))
    with pytest.raises(ProviderAccessDeniedError) as caught:
        await TavilyYouTubeDiscovery(FakeTavily(error=InvalidAPIKeyError("tvly-SECRET")), SETTINGS).search(DiscoveryQuery(query_text="q"))
    assert "tvly-SECRET" not in str(caught.value)


async def test_discovery_times_out_within_its_configured_bound():
    slow = TavilyDiscoverySettings(search_depth="basic", timeout_seconds=0.01, request_max_results=5)
    with pytest.raises(ProviderTimeoutError):
        await TavilyYouTubeDiscovery(FakeTavily(_response(), delay=1), slow).search(DiscoveryQuery(query_text="q"))


async def test_discovery_span_never_contains_the_query_or_titles():
    tracer, exporter = local_tracer()
    await TavilyYouTubeDiscovery(FakeTavily(_response()), SETTINGS, tracer=tracer).search(DiscoveryQuery(query_text="my private study question"))
    tracer.shutdown(1)
    text = repr(exporter.spans)
    assert "private study question" not in text and "Ohm's Law" not in text
    parent = next(span for span in exporter.spans if span.name == "media.discovery.search")
    assert parent.attributes["netra.evidence_count"] == 2 and parent.attributes["netra.rejected_count"] == 4


def test_general_web_search_is_not_callable():
    """General web search is out of current scope; its code is commented out."""

    assert not hasattr(TavilyYouTubeDiscovery, "search_web")


def test_titles_are_bounded_untrusted_text():
    assert clean_title("  a\u200bb\n c ") == "a b c"
    assert clean_title("x" * 1000) == "x" * 300
    assert clean_title(123) is None and clean_title("   ") is None


def test_client_construction_fails_closed_without_a_key():
    with pytest.raises(ProviderConfigurationError):
        TavilyYouTubeDiscovery.from_api_key("", SETTINGS)
