"""YouTube discovery results and the identity of the selected video.

current-scope.md: "YouTube discovery remains in scope even though
general web ingestion is deferred." This is search over YouTube through
the approved discovery adapter (Tavily, pinned in the runtime baseline),
producing candidate lectures — not web ingestion, and not a fetch of
page content.

The AgentSpec's requirement is "present numbered results and retain the
exact selected result". Numbering is therefore part of the data, not a
rendering detail: the student selects by saying "the second one", and
the number they heard has to mean the same video when the selection is
resolved. DiscoveryResultSet enforces that its ordinals are 1..n,
contiguous and unique, so a result set that cannot be spoken
unambiguously cannot be constructed.

Where the selection is *stored* is M1's: the session service owns
"ordered result selection" and the last stable result set. This module
owns the shape and the deterministic resolution; it holds no state.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class DiscoveryQuery(BaseModel):
    """What the student asked discovery for."""

    query_text: str = Field(min_length=1, max_length=500)
    learning_goal: Optional[str] = None
    """The current study context, used to judge relevance before
    committing to expensive processing. Optional: a student may search
    before any source is open."""
    max_results: int = Field(default=5, ge=1, le=20)
    """Bounded because the results are read aloud. A list nobody can hold
    in their head is not a usable selection, and every extra candidate is
    a video Netra might be asked to process."""


class DiscoveredVideo(BaseModel):
    """One candidate lecture, as Netra models it.

    A Netra-owned type, never a provider response object (multimedia.md:
    "Return Netra-owned contract types, not provider SDK objects").
    """

    result_ordinal: int = Field(ge=1)
    """The number the student hears. 1-based: "the first result"."""
    youtube_video_id: str = Field(min_length=1)
    """The canonical YouTube id. The exact selected identity the AgentSpec
    requires retaining — not the title, which is not unique, and not the
    URL, which carries tracking parameters that differ between
    searches."""
    title: str
    channel: Optional[str] = None
    duration_ms: Optional[int] = Field(default=None, ge=0)
    url: Optional[str] = None
    embeddable: Optional[bool] = None
    """Whether the owner permits embedded playback, when discovery
    reported it. None means unknown, and unknown is not permission — see
    netra_api.multimedia.video.readiness.assess_playback."""


class DiscoveryResultSet(BaseModel):
    """One numbered, stable list of candidates for one query."""

    result_set_id: UUID
    query: DiscoveryQuery
    results: List[DiscoveredVideo] = Field(default_factory=list)
    produced_at: datetime

    @model_validator(mode="after")
    def _check_stable_numbering(self) -> "DiscoveryResultSet":
        ordinals = [result.result_ordinal for result in self.results]
        if ordinals != list(range(1, len(self.results) + 1)):
            raise ValueError(
                "result_ordinal values must be 1..n in order, so a spoken "
                "number always identifies the same result"
            )
        video_ids = [result.youtube_video_id for result in self.results]
        if len(set(video_ids)) != len(video_ids):
            raise ValueError(
                "the same video must not appear twice: two numbers for one "
                "video make a selection ambiguous"
            )
        return self


class SelectedVideo(BaseModel):
    """The exact result a student chose, retained beyond the result set.

    Carries result_set_id and result_ordinal as well as the video id, so
    a later question can be traced to the list the student actually
    heard. A selection recorded as a bare video id cannot answer "which
    one did I pick?" after a second search.
    """

    result_set_id: UUID
    result_ordinal: int = Field(ge=1)
    youtube_video_id: str
    title: str
    channel: Optional[str] = None
    duration_ms: Optional[int] = Field(default=None, ge=0)
    embeddable: Optional[bool] = None
    selected_at: datetime


class RelevanceAssessment(str, Enum):
    """How clearly a candidate matches the student's stated learning goal."""

    CLEAR = "clear"
    AMBIGUOUS = "ambiguous"
    NOT_RELEVANT = "not_relevant"
    NOT_ASSESSED = "not_assessed"
    """No learning goal was available to judge against. Treated exactly
    like AMBIGUOUS by the processing gate: not knowing and not being sure
    lead to the same safe action."""
