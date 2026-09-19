"""C7 review draft: bounded public YouTube discovery payloads.

Not mounted on a route until Arshad and Ashlin review the shared contract.
These payloads never authorize a session, start ingestion or expose Tavily
objects. The route owner supplies authenticated scope and replay handling.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from netra_api.multimedia.discovery.models import DiscoveryQuery, DiscoveryResultSet


class PublicPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class YouTubeSearchRequest(PublicPayload):
    request_id: UUID
    expected_session_version: int = Field(ge=0)
    query_text: str = Field(min_length=1, max_length=500, pattern=r"\S")
    max_results: int = Field(default=5, ge=1, le=20)

    def to_query(self) -> DiscoveryQuery:
        # Preserve the student's exact query. Context/relevance is server-owned.
        return DiscoveryQuery(query_text=self.query_text, max_results=self.max_results)


class YouTubeResult(PublicPayload):
    result_ordinal: int = Field(ge=1, le=20)
    youtube_video_id: str = Field(pattern=r"^[A-Za-z0-9_-]{11}$")
    title: str = Field(min_length=1, max_length=300)
    channel: str | None = None
    duration_ms: Annotated[int, Field(ge=0)] | None = None
    embeddable: bool | None = None


class YouTubeSearchResponse(PublicPayload):
    request_id: UUID
    session_version: int = Field(ge=0)
    result_set_id: UUID
    produced_at: AwareDatetime
    results: list[YouTubeResult] = Field(max_length=20)

    @model_validator(mode="after")
    def stable_numbers(self) -> YouTubeSearchResponse:
        if [item.result_ordinal for item in self.results] != list(range(1, len(self.results) + 1)):
            raise ValueError("results must retain contiguous one-based order")
        ids = [item.youtube_video_id for item in self.results]
        if len(ids) != len(set(ids)):
            raise ValueError("a video may appear only once in a result set")
        return self


class YouTubeSelectionRequest(PublicPayload):
    request_id: UUID
    expected_session_version: int = Field(ge=0)
    result_set_id: UUID
    result_ordinal: int = Field(ge=1, le=20)


class YouTubeSelectionResponse(PublicPayload):
    request_id: UUID
    session_version: int = Field(ge=0)
    result_set_id: UUID
    selected_at: AwareDatetime
    selection: YouTubeResult


def public_results(
    result_set: DiscoveryResultSet, *, request_id: UUID, session_version: int
) -> YouTubeSearchResponse:
    """Project adapter results explicitly; URLs/snippets are never public fields."""
    return YouTubeSearchResponse(
        request_id=request_id,
        session_version=session_version,
        result_set_id=result_set.result_set_id,
        produced_at=result_set.produced_at,
        results=[YouTubeResult(
            result_ordinal=item.result_ordinal,
            youtube_video_id=item.youtube_video_id,
            title=item.title,
            channel=item.channel,
            duration_ms=item.duration_ms,
            embeddable=item.embeddable,
        ) for item in result_set.results],
    )
