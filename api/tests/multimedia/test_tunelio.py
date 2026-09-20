"""The Tunelio resolver: what it refuses, what it costs, what it trusts.

Fakes throughout. The endpoint shapes are transcribed from live calls made
on 20 September 2026 (``/info`` and ``/create`` against the configured key);
that run is recorded in docs/team/integration-status.md.
"""

from __future__ import annotations

import pytest

from netra_api.multimedia.providers.errors import (MalformedProviderResponseError,
                                                   ProviderQuotaExceededError,
                                                   ProviderRejectedMediaError)
from netra_api.multimedia.providers.tunelio import (CREATE_CREDITS, INFO_CREDITS, ResolutionReport,
                                                    TunelioSettings, TunelioYouTubeResolver,
                                                    youtube_watch_url)

VIDEO_ID = "dQw4w9WgXcQ"
LINK = "https://tunelio.dev/dl/abc123/video.mp4"

# Shapes taken from the live responses.
INFO = {"title": "Ohm's Law — full lecture", "duration_seconds": 213, "duration_str": "03:33",
        "thumbnail": "https://i.ytimg.com/vi/x/hqdefault.jpg",
        "formats": [{"quality": "360p", "file_size": 11_838_000}]}
CREATED = {"url": LINK, "filename": "lecture.mp4", "file_size": 11_838_000, "file_size_str": "11.29 MB",
           "quality": "360p", "mode": "video", "status": "ok", "expires": 1789000000}


def _settings(**overrides):
    values = dict(quality="360p", timeout_seconds=30.0, max_duration_seconds=3600, max_bytes=500_000_000)
    values.update(overrides)
    return TunelioSettings(**values)


class Response:
    def __init__(self, status_code, body=None, raises=False):
        self.status_code, self._body, self._raises = status_code, body, raises

    def json(self):
        if self._raises:
            raise ValueError("not json")
        return self._body


class Client:
    """Records every request so the test can assert what was actually asked."""

    def __init__(self, *responses):
        self._responses = list(responses)
        self.calls = []

    async def get(self, path, params=None):
        self.calls.append((path, dict(params or {})))
        return self._responses.pop(0)


async def _resolve(*responses, settings=None, report=None):
    client = Client(*responses)
    resolver = TunelioYouTubeResolver(client, settings or _settings())
    media = await resolver.resolve(VIDEO_ID, report=report)
    return media, client


# --- the happy path ----------------------------------------------------------------


async def test_a_youtube_id_resolves_to_a_direct_link():
    media, client = await _resolve(Response(200, INFO), Response(200, CREATED))
    assert media.url == LINK
    assert media.duration_seconds == 213
    assert media.content_bytes == 11_838_000
    assert media.title == "Ohm's Law — full lecture"
    assert [path for path, _ in client.calls] == ["/info", "/create"]


async def test_the_watch_url_is_built_here_not_taken_from_a_caller():
    """A crafted link must never send a student's request off YouTube."""

    _, client = await _resolve(Response(200, INFO), Response(200, CREATED))
    assert client.calls[0][1]["url"] == "https://www.youtube.com/watch?v=" + VIDEO_ID
    assert client.calls[1][1]["quality"] == "360p"


async def test_credits_are_counted_for_both_calls():
    report = ResolutionReport()
    media, _ = await _resolve(Response(200, INFO), Response(200, CREATED), report=report)
    assert report.credits_spent == INFO_CREDITS + CREATE_CREDITS == 16
    assert media.credits_spent == 16


@pytest.mark.parametrize("bad", ["", "short", "waytoolongforanid", "has space!!", None, 12345])
def test_only_a_real_youtube_id_is_accepted(bad):
    with pytest.raises(ProviderRejectedMediaError):
        youtube_watch_url(bad)


# --- refusals that save credits or bytes -------------------------------------------


async def test_a_video_over_the_duration_limit_is_refused_before_create_is_called():
    """Metadata first: a too-long video costs 6 credits, not 16."""

    report = ResolutionReport()
    client = Client(Response(200, dict(INFO, duration_seconds=7200)))
    resolver = TunelioYouTubeResolver(client, _settings(max_duration_seconds=3600))
    with pytest.raises(ProviderRejectedMediaError):
        await resolver.resolve(VIDEO_ID, report=report)
    assert [path for path, _ in client.calls] == ["/info"]
    assert report.credits_spent == INFO_CREDITS
    assert report.rejections == ["too_long"]


async def test_a_file_over_the_byte_limit_is_refused_before_anything_streams_it():
    report = ResolutionReport()
    client = Client(Response(200, INFO), Response(200, dict(CREATED, file_size=900_000_000)))
    resolver = TunelioYouTubeResolver(client, _settings(max_bytes=500_000_000))
    with pytest.raises(ProviderRejectedMediaError):
        await resolver.resolve(VIDEO_ID, report=report)
    assert report.rejections == ["too_large"]


# --- untrusted provider output -----------------------------------------------------


@pytest.mark.parametrize("link", [
    "http://tunelio.dev/dl/x.mp4",            # not HTTPS
    "https://evil.example/dl/x.mp4",          # not Tunelio
    "https://tunelio.dev.evil.example/x.mp4",  # suffix trick
    "", None, 42,
])
async def test_a_link_that_is_not_a_tunelio_https_url_is_malformed(link):
    """The worker is about to fetch this. It must not fetch anywhere."""

    with pytest.raises(MalformedProviderResponseError):
        await _resolve(Response(200, INFO), Response(200, dict(CREATED, url=link)))


async def test_a_subdomain_of_tunelio_is_accepted():
    media, _ = await _resolve(Response(200, INFO),
                              Response(200, dict(CREATED, url="https://cdn.tunelio.dev/dl/x.mp4")))
    assert media.url == "https://cdn.tunelio.dev/dl/x.mp4"


async def test_an_overlong_title_is_truncated_and_never_becomes_a_filename():
    media, _ = await _resolve(Response(200, dict(INFO, title="x" * 900)), Response(200, CREATED))
    assert len(media.title) == 300


async def test_a_title_that_is_not_text_is_dropped():
    media, _ = await _resolve(Response(200, dict(INFO, title={"nested": "object"})), Response(200, CREATED))
    assert media.title is None


# --- provider failures are typed, never raw bodies ---------------------------------


@pytest.mark.parametrize("status", [402, 429])
async def test_exhausted_credits_are_reported_as_quota(status):
    with pytest.raises(ProviderQuotaExceededError):
        await _resolve(Response(status, {}))


@pytest.mark.parametrize("status", [403, 404, 500, 503])
async def test_any_other_status_is_a_typed_provider_error_without_the_body(status):
    """403 here is usually Cloudflare; the caller never sees the page."""

    with pytest.raises(MalformedProviderResponseError) as caught:
        await _resolve(Response(status, {"secret": "do not leak"}))
    assert "do not leak" not in str(caught.value)


async def test_an_unreadable_body_is_malformed_not_a_crash():
    with pytest.raises(MalformedProviderResponseError):
        await _resolve(Response(200, None, raises=True))


async def test_a_non_object_body_is_malformed():
    with pytest.raises(MalformedProviderResponseError):
        await _resolve(Response(200, ["not", "an", "object"]))
