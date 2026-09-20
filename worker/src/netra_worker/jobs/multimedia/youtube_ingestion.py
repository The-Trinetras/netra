"""Bring a YouTube lecture into private storage so it can be analysed.

Decision M3-YT-ANALYSIS. TwelveLabs cannot read a YouTube page, so the video
has to exist as bytes Netra controls before anything indexes it:

    Tunelio resolves a direct link  ->  stream into the private bucket
    ->  enqueue index_video  ->  derive_video_evidence

This job is the first arrow and the second. It never calls TwelveLabs, and it
never keeps the Tunelio link: that link is temporary, and a stored temporary
link would read like a durable media location later.

Streaming, not buffering. A lecture is tens of megabytes and the worker runs
beside the API; ``ObjectStorageProvider`` takes the whole object, so the size
ceiling from ``TunelioSettings.max_bytes`` is enforced twice — once on the
provider's declared size before the fetch, and again on the bytes actually
received, because a provider's declared size is untrusted like the rest of
its output.

Idempotence: the object key is derived from the source version, so a retry
overwrites the same key with the same bytes rather than accumulating copies,
and the follow-on job is enqueued under a key derived from the version too
(the scheduler is idempotent by that key). Re-running a completed stage is
therefore free rather than a second download — which matters, because each
resolution spends Tunelio credits.
"""

from __future__ import annotations

import logging
from typing import Any, Optional, Protocol
from uuid import UUID

from pydantic import Field

from netra_api.content.telemetry import log_event
from netra_api.multimedia.providers.errors import (MalformedProviderResponseError,
                                                   ProviderRejectedMediaError)
from netra_worker.jobs.multimedia.base import MultimediaJobPayload
from netra_worker.jobs.multimedia.video import IndexVideoPayload

INGEST_STAGE = "ingest_youtube_video"
INDEX_JOB_TYPE = "index_video"
CONTENT_TYPE = "video/mp4"

_LOG = logging.getLogger(__name__)


class MediaResolver(Protocol):
    """netra_api.multimedia.providers.tunelio.TunelioYouTubeResolver."""

    async def resolve(self, video_id: str, *, report: Any = None) -> Any:
        ...


class MediaFetcher(Protocol):
    """Reads the resolved link. Separate from the resolver so the bytes never
    travel through the provider adapter, which deals only in metadata."""

    async def fetch(self, url: str, *, max_bytes: int) -> bytes:
        ...


class ObjectWriter(Protocol):
    async def put_object(self, key: str, data: bytes, content_type: str) -> Any:
        ...


class Scheduler(Protocol):
    def enqueue(self, job_type: str, payload: Any, idempotency_key: str) -> Any:
        ...


class IngestYouTubeVideoPayload(MultimediaJobPayload):
    video_id: UUID
    """The canonical VideoAsset id, not YouTube's."""
    external_ref: str = Field(min_length=11, max_length=11)
    """The YouTube video id. Eleven characters, validated again by the
    resolver before any request is built from it."""


def media_object_key(account_id: UUID, source_version_id: UUID) -> str:
    """Private, account-scoped and stable for one source version."""

    return f"videos/{account_id}/{source_version_id}/media.mp4"


class IngestYouTubeVideoJob:
    """Structurally implements JobHandler[IngestYouTubeVideoPayload]."""

    def __init__(self, resolver: MediaResolver, fetcher: MediaFetcher, storage: ObjectWriter,
                 *, scheduler: Scheduler, account_id_for: Any, max_bytes: int) -> None:
        self._resolver = resolver
        self._fetcher = fetcher
        self._storage = storage
        self._scheduler = scheduler
        # The worker never re-reads account-scoped state on its own; the
        # composition supplies the lookup that owns it.
        self._account_id_for = account_id_for
        self._max_bytes = max_bytes

    async def handle(self, payload: IngestYouTubeVideoPayload) -> None:
        account_id = await _maybe_await(self._account_id_for(payload.source_version_id))
        if account_id is None:
            # The source version was deleted while this job waited. Deleting a
            # source must not be undone by a job that outlived it.
            log_event(_LOG, "youtube_ingestion_skipped", component="ingestion",
                      stage=INGEST_STAGE, reason="source_version_absent")
            return

        media = await self._resolver.resolve(payload.external_ref)
        declared = getattr(media, "content_bytes", None)
        if isinstance(declared, int) and declared > self._max_bytes:
            raise ProviderRejectedMediaError("tunelio", "resolved media is larger than the configured maximum")

        data = await self._fetcher.fetch(media.url, max_bytes=self._max_bytes)
        if not data:
            raise MalformedProviderResponseError("tunelio", "resolved link returned no bytes")

        key = media_object_key(account_id, payload.source_version_id)
        await _maybe_await(self._storage.put_object(key, data, CONTENT_TYPE))
        log_event(_LOG, "youtube_ingestion_stored", component="ingestion", stage=INGEST_STAGE,
                  source_version_id=str(payload.source_version_id), bytes=len(data),
                  credits_spent=getattr(media, "credits_spent", None))

        # The next stage reads the bucket, never the Tunelio link: that link
        # expires, and the bytes Netra stored are the ones it analysed.
        self._scheduler.enqueue(
            INDEX_JOB_TYPE,
            IndexVideoPayload(source_id=payload.source_id, source_version_id=payload.source_version_id,
                              video_id=payload.video_id, external_ref=key, content_type=CONTENT_TYPE),
            idempotency_key=f"{INDEX_JOB_TYPE}:{payload.source_version_id}",
        )


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value


class HttpMediaFetcher:
    """Downloads a resolved link, refusing anything over the byte ceiling.

    Enforced while streaming rather than after: a provider that ignores its
    own declared size must not be able to make the worker hold an arbitrary
    amount of memory.
    """

    def __init__(self, client: Any) -> None:
        self._client = client

    async def fetch(self, url: str, *, max_bytes: int) -> bytes:
        chunks: list[bytes] = []
        total = 0
        async with self._client.stream("GET", url) as response:
            status = getattr(response, "status_code", None)
            if status != 200:
                raise MalformedProviderResponseError("tunelio", f"media link returned status {status}",
                                                     field="status")
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise ProviderRejectedMediaError("tunelio", "media exceeded the configured maximum while streaming")
                chunks.append(chunk)
        return b"".join(chunks)
