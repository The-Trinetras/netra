"""Registered Tavily adapter for YouTube discovery.

Implements netra_api.multimedia.discovery.adapter.VideoDiscoveryProvider.
Tavily is the approved discovery provider (runtime-baseline.md:
"Tavily discovery remains relevant to in-scope YouTube search";
tavily-python==0.7.27). This adapter is YouTube *discovery* only:

- every request is restricted to YouTube domains (include_domains);
- every result must parse to a canonical 11-character YouTube video id,
  otherwise it is dropped and counted — a channel, playlist, article or
  any other page never becomes a candidate;
- the result's page content/snippet is discarded, never returned, stored
  or traced. Only the title is kept, as untrusted display text.

General web search/ingestion is removed from current scope
(current-scope.md: general web ingestion is deferred). The general-web
search path is preserved below as commented-out reference code so it can
be re-enabled after a product scope decision; it is not callable.

Provider facts Tavily does not report (duration, channel, embeddability)
stay None. None is "unknown", and unknown is not permission: playback and
analysis readiness are decided separately (video/readiness.py).

The SDK is pinned but not installed where this code has run; the client
is injected, and ``from_api_key`` imports the SDK lazily. Exact response
keys are transcribed from the 0.7.27 README (results[].title/url) and are
unverified until an authorized install (M3-SDK-2 in the M3 handoff).
"""

from __future__ import annotations

import functools
import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, List, Literal, Mapping, Optional
from urllib.parse import parse_qs, urlsplit

from pydantic import BaseModel, Field

from netra_api.multimedia.discovery.adapter import number_results
from netra_api.multimedia.discovery.models import (
    DiscoveredVideo,
    DiscoveryQuery,
    DiscoveryResultSet,
)
from netra_api.multimedia.providers.calls import CancellationSignal, call_provider
from netra_api.multimedia.providers.errors import (
    MalformedProviderResponseError,
    ProviderConfigurationError,
)
from netra_api.multimedia.tracing import media_span, record_outcome
from netra_api.platform.tracing import Tracer

TAVILY_PROVIDER = "tavily"

YOUTUBE_DOMAINS: tuple[str, ...] = ("youtube.com", "youtu.be")
"""The only domains a discovery request may target. Not configurable:
widening it would reinstate deferred general web search."""

_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_WATCH_HOSTS = frozenset({"youtube.com", "www.youtube.com", "m.youtube.com"})
_SHORT_HOSTS = frozenset({"youtu.be", "www.youtu.be"})
_PATH_PREFIXES = ("/embed/", "/shorts/", "/live/", "/v/")
MAX_TITLE_CHARS = 300


def parse_youtube_video_id(url: Any) -> Optional[str]:
    """Canonical YouTube video id for a video URL, or None.

    Accepts watch, embed, shorts, live and youtu.be forms over http(s).
    Rejects channels, playlists without a video, search pages and every
    other host. The id must match YouTube's 11-character alphabet.
    """

    if not isinstance(url, str) or len(url) > 2048:
        return None
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    if parts.scheme not in ("http", "https"):
        return None
    host = (parts.hostname or "").lower()
    candidate: Optional[str] = None
    if host in _SHORT_HOSTS:
        candidate = parts.path.lstrip("/").split("/")[0]
    elif host in _WATCH_HOSTS:
        if parts.path == "/watch":
            values = parse_qs(parts.query).get("v", [])
            candidate = values[0] if len(values) == 1 else None
        else:
            for prefix in _PATH_PREFIXES:
                if parts.path.startswith(prefix):
                    candidate = parts.path[len(prefix):].split("/")[0]
                    break
    if candidate and _VIDEO_ID.match(candidate):
        return candidate
    return None


def canonical_watch_url(video_id: str) -> str:
    """Tracking-free URL for a video id; the id, not the URL, is the identity."""

    return f"https://www.youtube.com/watch?v={video_id}"


def clean_title(value: Any) -> Optional[str]:
    """Untrusted display text: control characters removed, whitespace collapsed, bounded."""

    if not isinstance(value, str):
        return None
    text = "".join(ch if unicodedata.category(ch)[0] != "C" else " " for ch in value)
    text = " ".join(text.split())
    if not text:
        return None
    return text[:MAX_TITLE_CHARS]


class TavilyDiscoverySettings(BaseModel):
    """Explicit configuration; no defaults for cost-relevant settings."""

    search_depth: Literal["basic", "advanced"]
    timeout_seconds: float = Field(gt=0)
    request_max_results: int = Field(ge=1, le=20)
    """How many raw results to request. Non-video results are dropped, so
    this may exceed DiscoveryQuery.max_results; the returned list is still
    capped at the query's max_results."""


@dataclass
class DiscoveryReport:
    returned: int = 0
    kept: int = 0
    not_a_video: int = 0
    untitled: int = 0
    duplicates: int = 0


class TavilyYouTubeDiscovery:
    """VideoDiscoveryProvider over Tavily, restricted to YouTube videos."""

    def __init__(
        self,
        client: Any,
        settings: TavilyDiscoverySettings,
        *,
        tracer: Optional[Tracer] = None,
    ) -> None:
        self._client = client
        self._settings = settings
        self._tracer = tracer
        self.last_report = DiscoveryReport()

    @classmethod
    def from_api_key(cls, api_key: str, settings: TavilyDiscoverySettings, *, tracer: Optional[Tracer] = None) -> "TavilyYouTubeDiscovery":
        if not api_key:
            raise ProviderConfigurationError(TAVILY_PROVIDER, "no API key configured")
        try:
            from tavily import AsyncTavilyClient  # type: ignore[import-not-found]
        except ImportError as exc:  # pinned, not installed here
            raise ProviderConfigurationError(TAVILY_PROVIDER, "tavily-python SDK is not installed") from exc
        return cls(AsyncTavilyClient(api_key=api_key), settings, tracer=tracer)

    async def search(
        self, query: DiscoveryQuery, *, cancellation: Optional[CancellationSignal] = None
    ) -> List[DiscoveredVideo]:
        report = DiscoveryReport()
        self.last_report = report
        with media_span(self._tracer, "media.discovery.search", operation="youtube_discovery", provider=TAVILY_PROVIDER) as span:
            # functools.partial keeps an async client's coroutine function
            # recognisable, so it is awaited on the loop instead of being
            # created in a worker thread (call_provider._invoke).
            response = await call_provider(
                functools.partial(
                    self._client.search,
                    query=query.query_text,
                    search_depth=self._settings.search_depth,
                    max_results=self._settings.request_max_results,
                    include_domains=list(YOUTUBE_DOMAINS),
                    include_answer=False,
                    include_raw_content=False,
                    include_images=False,
                    timeout=self._settings.timeout_seconds,
                ),
                provider=TAVILY_PROVIDER,
                operation="tavily_search",
                timeout_seconds=self._settings.timeout_seconds,
                cancellation=cancellation,
                tracer=self._tracer,
            )
            results = _results_of(response)
            report.returned = len(results)
            candidates: List[DiscoveredVideo] = []
            seen: set[str] = set()
            for item in results:
                video_id = parse_youtube_video_id(_field(item, "url"))
                if video_id is None:
                    report.not_a_video += 1
                    continue
                title = clean_title(_field(item, "title"))
                if title is None:
                    report.untitled += 1
                    continue
                if video_id in seen:
                    report.duplicates += 1
                    continue
                seen.add(video_id)
                # Content/snippet deliberately not read: general page text
                # is out of scope and untrusted.
                candidates.append(
                    DiscoveredVideo(
                        result_ordinal=len(candidates) + 1,
                        youtube_video_id=video_id,
                        title=title,
                        url=canonical_watch_url(video_id),
                    )
                )
            numbered = number_results(candidates)[: query.max_results]
            report.kept = len(numbered)
            record_outcome(
                span,
                "results" if numbered else "no_results",
                netra_evidence_count=len(numbered),
                netra_rejected_count=report.not_a_video + report.untitled,
            )
            return numbered

    # -------------------------------------------------------------------
    # General web search — REMOVED from current product scope.
    #
    # current-scope.md defers general web ingestion; agent-boundaries.md
    # lists the historical `search_web` tool as deferred. The code below is
    # preserved for reference only and is intentionally commented out so
    # no caller can reach it. Re-enabling it needs an approved scope change,
    # a reviewed tool contract (untrusted output, safe fetch rules) and
    # M1 registration; it must not be switched on by uncommenting alone.
    #
    # class WebSearchResult(BaseModel):
    #     title: str
    #     url: str
    #     snippet: str
    #
    # async def search_web(
    #     self, query_text: str, *, max_results: int, cancellation: Optional[CancellationSignal] = None
    # ) -> List["WebSearchResult"]:
    #     response = await call_provider(
    #         lambda: self._client.search(
    #             query=query_text,
    #             search_depth=self._settings.search_depth,
    #             max_results=max_results,
    #             include_answer=False,
    #             include_raw_content=False,
    #             timeout=self._settings.timeout_seconds,
    #         ),
    #         provider=TAVILY_PROVIDER,
    #         operation="tavily_web_search",
    #         timeout_seconds=self._settings.timeout_seconds,
    #         cancellation=cancellation,
    #         tracer=self._tracer,
    #     )
    #     return [
    #         WebSearchResult(
    #             title=clean_title(_field(item, "title")) or "",
    #             url=str(_field(item, "url") or ""),
    #             snippet=str(_field(item, "content") or "")[:1000],
    #         )
    #         for item in _results_of(response)[:max_results]
    #     ]
    # -------------------------------------------------------------------


def _field(item: Any, name: str) -> Any:
    if isinstance(item, Mapping):
        return item.get(name)
    return getattr(item, name, None)


def _results_of(response: Any) -> list:
    results = _field(response, "results")
    if not isinstance(results, list):
        raise MalformedProviderResponseError(TAVILY_PROVIDER, "response has no results list", field="results")
    return results


def build_result_set(
    query: DiscoveryQuery,
    results: List[DiscoveredVideo],
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    id_factory: Callable[[], uuid.UUID] = uuid.uuid4,
) -> DiscoveryResultSet:
    """Wrap adapter output in a stable, numbered result set for M1 to store."""

    return DiscoveryResultSet(result_set_id=id_factory(), query=query, results=results, produced_at=clock())
