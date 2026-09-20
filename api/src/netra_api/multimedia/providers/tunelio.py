"""Registered Tunelio adapter: a YouTube link becomes a direct media link.

Decision M3-YT-ANALYSIS. TwelveLabs cannot read a YouTube page; it needs a
media URL it can fetch. Tunelio resolves a watch URL into a temporary direct
CDN link, which the worker streams into the private bucket before TwelveLabs
reads it back through a presigned URL (M3-MEDIA-URL). This adapter is only
that resolution step: it never downloads bytes, never stores anything and
never touches TwelveLabs.

The risks the owner accepted with M3-YT-ANALYSIS stay true and stay visible:
downloading breaks YouTube's Terms of Service and may infringe copyright on
lecture videos; the service works around YouTube's bot checks and can stop
without notice; and a student's video choice is sent to a third party. The
adapter is off unless ``NETRA_TUNELIO_API_KEY`` is set, and every call
records the credits it spent so the cost stays countable (6 per /info, 10
per /create as recorded in integration-status.md).

Endpoints, verified live on 20 September 2026 against the documented key:

    GET https://tunelio.dev/info?url=<watch url>
    GET https://tunelio.dev/create?url=<watch url>&quality=<label>
    Authorization: Bearer <key>

``/info`` returns title, duration_seconds and a formats list; ``/create``
returns {url, filename, file_size, quality, expires, ...} where ``url`` is a
temporary direct link that serves ``video/mp4``. Tunelio sits behind
Cloudflare and answers 403 (code 1010) to a client that sends no
``User-Agent``; this adapter identifies itself as Netra rather than
impersonating a browser.

Provider output is untrusted data (CLAUDE.md): the title is display text
only, and the returned link is validated as an HTTPS URL on Tunelio's own
host before anything is asked to fetch it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from netra_api.multimedia.providers.calls import CancellationSignal, call_provider
from netra_api.multimedia.providers.errors import (
    MalformedProviderResponseError,
    ProviderConfigurationError,
    ProviderQuotaExceededError,
    ProviderRejectedMediaError,
)
from netra_api.platform.tracing import Tracer

TUNELIO_PROVIDER = "tunelio"
BASE_URL = "https://tunelio.dev"
USER_AGENT = "netra-worker/1.0"
"""Identifies Netra. Tunelio's edge rejects a request with no User-Agent."""

INFO_CREDITS = 6
CREATE_CREDITS = 10

MAX_TITLE_CHARS = 300


class TunelioSettings(BaseModel):
    """Explicit configuration; no defaults for cost-relevant settings."""

    quality: str = Field(min_length=2)
    """The format label asked of /create, e.g. "360p". A lecture is read for
    its slides, not its cinematography: the smallest usable quality keeps the
    download, the bucket and the provider's analysis budget small."""
    timeout_seconds: float = Field(gt=0)
    max_duration_seconds: int = Field(gt=0)
    """Refuse a video longer than this before spending /create credits.
    TwelveLabs' own sync analysis stops at one hour."""
    max_bytes: int = Field(gt=0)
    """Refuse a file larger than this before anything streams it."""


class ResolvedMedia(BaseModel):
    """One temporary direct link, with what it cost to obtain."""

    url: str
    filename: Optional[str] = None
    content_bytes: Optional[int] = Field(default=None, ge=0)
    quality: Optional[str] = None
    duration_seconds: Optional[int] = Field(default=None, ge=0)
    title: Optional[str] = None
    """Untrusted display text, truncated. Never a filename or a prompt."""
    credits_spent: int = Field(ge=0)


@dataclass
class ResolutionReport:
    """What one resolution cost and rejected, for the caller to log."""

    credits_spent: int = 0
    rejections: List[str] = field(default_factory=list)


def _text(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())
    return cleaned[:MAX_TITLE_CHARS] or None


def _int(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value) if value >= 0 else None


def _validated_link(raw: Any) -> str:
    """An HTTPS link on Tunelio's own host, or a malformed-response error.

    The worker is about to fetch whatever comes back. A provider that
    returned an http:// link, a link to somewhere else, or no link at all is
    a malformed response, not a media source.
    """

    if not isinstance(raw, str) or not raw:
        raise MalformedProviderResponseError(TUNELIO_PROVIDER, "no download link returned", field="url")
    parts = urlsplit(raw)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not (host == "tunelio.dev" or host.endswith(".tunelio.dev")):
        raise MalformedProviderResponseError(TUNELIO_PROVIDER, "download link is not a Tunelio HTTPS link",
                                             field="url")
    return raw


class TunelioYouTubeResolver:
    """Resolves a YouTube video id into a temporary direct media link."""

    def __init__(self, client: Any, settings: TunelioSettings, *, tracer: Optional[Tracer] = None) -> None:
        self._client = client
        self._settings = settings
        self._tracer = tracer

    @classmethod
    def from_api_key(cls, api_key: str, settings: TunelioSettings, *,
                     tracer: Optional[Tracer] = None) -> "TunelioYouTubeResolver":
        if not api_key:
            raise ProviderConfigurationError(TUNELIO_PROVIDER, "no API key configured")
        try:
            import httpx  # type: ignore[import-not-found]
        except ImportError as exc:  # pinned, but keep the failure typed
            raise ProviderConfigurationError(TUNELIO_PROVIDER, "httpx is not installed") from exc

        client = httpx.AsyncClient(
            base_url=BASE_URL,
            timeout=settings.timeout_seconds,
            follow_redirects=True,
            headers={"Authorization": f"Bearer {api_key}", "User-Agent": USER_AGENT,
                     "Accept": "application/json"},
        )
        return cls(client, settings, tracer=tracer)

    async def resolve(self, video_id: str, *, report: Optional[ResolutionReport] = None,
                      cancellation: Optional[CancellationSignal] = None) -> ResolvedMedia:
        """Metadata first, then a link — so a too-long video costs 6, not 16."""

        report = report if report is not None else ResolutionReport()
        watch_url = youtube_watch_url(video_id)

        info = await self._get("/info", {"url": watch_url}, "tunelio_info", cancellation)
        report.credits_spent += INFO_CREDITS
        duration = _int(info.get("duration_seconds"))
        if duration is not None and duration > self._settings.max_duration_seconds:
            report.rejections.append("too_long")
            raise ProviderRejectedMediaError(
                TUNELIO_PROVIDER,
                f"video is longer than the configured maximum of {self._settings.max_duration_seconds}s")

        created = await self._get("/create", {"url": watch_url, "quality": self._settings.quality},
                                  "tunelio_create", cancellation)
        report.credits_spent += CREATE_CREDITS
        size = _int(created.get("file_size"))
        if size is not None and size > self._settings.max_bytes:
            report.rejections.append("too_large")
            raise ProviderRejectedMediaError(
                TUNELIO_PROVIDER, f"media is larger than the configured maximum of {self._settings.max_bytes} bytes")

        return ResolvedMedia(
            url=_validated_link(created.get("url")),
            filename=_text(created.get("filename")),
            content_bytes=size,
            quality=_text(created.get("quality")),
            duration_seconds=duration,
            title=_text(info.get("title")),
            credits_spent=report.credits_spent,
        )

    async def _get(self, path: str, params: dict[str, str], operation: str,
                   cancellation: Optional[CancellationSignal]) -> dict[str, Any]:
        response = await call_provider(
            lambda: self._client.get(path, params=params),
            provider=TUNELIO_PROVIDER,
            operation=operation,
            timeout_seconds=self._settings.timeout_seconds,
            cancellation=cancellation,
            tracer=self._tracer,
        )
        status = getattr(response, "status_code", None)
        if status == 402 or status == 429:
            raise ProviderQuotaExceededError(TUNELIO_PROVIDER, "Tunelio credits or rate limit exhausted")
        if status != 200:
            # 403 here is usually Cloudflare, not authorization; either way the
            # caller learns the provider refused, never the response body.
            raise MalformedProviderResponseError(TUNELIO_PROVIDER, f"{path} returned status {status}",
                                                 field="status")
        try:
            body = response.json()
        except Exception as exc:  # noqa: BLE001 - any unparseable body is malformed
            raise MalformedProviderResponseError(TUNELIO_PROVIDER, f"{path} returned an unreadable body") from exc
        if not isinstance(body, dict):
            raise MalformedProviderResponseError(TUNELIO_PROVIDER, f"{path} did not return an object")
        return body

    async def aclose(self) -> None:
        close = getattr(self._client, "aclose", None)
        if close is not None:
            await close()


def youtube_watch_url(video_id: str) -> str:
    """The canonical watch URL for a validated 11-character YouTube id.

    Built here rather than accepting a caller's URL so a crafted link can
    never send a student's request somewhere other than YouTube.
    """

    if not _is_youtube_id(video_id):
        raise ProviderRejectedMediaError(TUNELIO_PROVIDER, "not a YouTube video id")
    return f"https://www.youtube.com/watch?v={video_id}"


def _is_youtube_id(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 11:
        return False
    return all(c.isalnum() or c in "-_" for c in value)
