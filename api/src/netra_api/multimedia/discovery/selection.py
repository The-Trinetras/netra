"""Deterministic selection from a numbered result set, and the cost gate.

Two rules from the prompt and the AgentSpec drive this module:

- "Retain selected identity and stable numbered-result semantics" —
  resolving "the second one" is arithmetic over a stored list, not a
  model decision. It is in the deterministic fast lane alongside the
  other navigation commands.
- "Ambiguous relevance must not trigger arbitrary costly processing" —
  the AgentSpec says "unclear relevance prompts clarification before
  expensive processing". Indexing a 90-minute lecture with a provider
  because a search result looked roughly right is exactly the spend this
  gate exists to stop.

The gate returns a decision; it does not enqueue anything. M1 owns the
session state that records the selection and M2 owns the job queue that
would run the work, so this module's job is to say what may happen, and
let the owners of those boundaries do it.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel

from netra_api.multimedia.discovery.models import (
    DiscoveryResultSet,
    RelevanceAssessment,
    SelectedVideo,
)


class UnknownResultError(LookupError):
    """Raised when a selection names an ordinal the result set does not contain.

    Fails closed rather than clamping to the nearest result. A student
    who says "the sixth one" after hearing five must be asked again, not
    quietly given the fifth: selecting the wrong lecture is not a
    recoverable error once processing has been paid for.
    """

    def __init__(self, result_ordinal: int, available: int) -> None:
        self.result_ordinal = result_ordinal
        self.available = available
        super().__init__(
            f"result {result_ordinal} does not exist; {available} results were offered"
        )


def select_by_ordinal(
    result_set: DiscoveryResultSet, result_ordinal: int, selected_at: datetime
) -> SelectedVideo:
    """Resolve a spoken result number to the exact video it identified."""

    for result in result_set.results:
        if result.result_ordinal == result_ordinal:
            return SelectedVideo(
                result_set_id=result_set.result_set_id,
                result_ordinal=result.result_ordinal,
                youtube_video_id=result.youtube_video_id,
                title=result.title,
                channel=result.channel,
                duration_ms=result.duration_ms,
                embeddable=result.embeddable,
                selected_at=selected_at,
            )
    raise UnknownResultError(result_ordinal, len(result_set.results))


def select_by_video_id(
    result_set: DiscoveryResultSet, youtube_video_id: str, selected_at: datetime
) -> SelectedVideo:
    """Resolve a pasted URL's video id against the list that was offered.

    Pasting a URL is optional in the AgentSpec, and it still resolves
    through the result set rather than around it: a video that was never
    offered has no ordinal, so a selection of it could not be traced back
    to anything the student heard.
    """

    for result in result_set.results:
        if result.youtube_video_id == youtube_video_id:
            return select_by_ordinal(result_set, result.result_ordinal, selected_at)
    raise UnknownResultError(0, len(result_set.results))


class ProcessingDecision(str, Enum):
    """What may be done with a selected video, before anything is spent."""

    PROCEED = "proceed"
    """Relevance is clear and the video is analysable. Costly processing
    may be enqueued."""
    CLARIFY = "clarify"
    """Ask the student before spending anything. The safe answer for both
    ambiguous and unassessed relevance."""
    REJECT = "reject"
    """Do not process: the student's own goal rules it out, or the media
    cannot be ingested at all."""


class ProcessingGate(BaseModel):
    """The decision plus the reason it was reached.

    The reason travels with the decision because CLARIFY is useless
    without it: "which of these did you mean?" and "this is an hour long,
    should I still analyse it?" are different questions.
    """

    decision: ProcessingDecision
    reason: str
    clarification_prompt: Optional[str] = None
    """What to ask the student. Present only for CLARIFY."""


def assess_processing(
    selection: SelectedVideo,
    relevance: RelevanceAssessment,
    *,
    ingestible: Optional[bool] = None,
    max_duration_ms: Optional[int] = None,
) -> ProcessingGate:
    """Decide whether a selected video may be sent for costly processing.

    ingestible is what the provider adapter reported about this media,
    or None when nothing has checked yet. None does not block the
    decision — indexing is how ingestibility is usually discovered — but
    an explicit False does.

    max_duration_ms, when set, turns an over-long lecture into a
    clarification rather than a refusal: the student may genuinely want
    it, and Netra should ask rather than decide for them.
    """

    if ingestible is False:
        return ProcessingGate(
            decision=ProcessingDecision.REJECT,
            reason="the provider cannot ingest this video",
        )
    if relevance is RelevanceAssessment.NOT_RELEVANT:
        return ProcessingGate(
            decision=ProcessingDecision.REJECT,
            reason="the selected video does not match the current learning goal",
        )
    if relevance in (RelevanceAssessment.AMBIGUOUS, RelevanceAssessment.NOT_ASSESSED):
        return ProcessingGate(
            decision=ProcessingDecision.CLARIFY,
            reason="relevance to the learning goal is not established",
            clarification_prompt=(
                f"Do you want Netra to analyse {selection.title}? "
                "Analysing a lecture takes a while."
            ),
        )
    if (
        max_duration_ms is not None
        and selection.duration_ms is not None
        and selection.duration_ms > max_duration_ms
    ):
        return ProcessingGate(
            decision=ProcessingDecision.CLARIFY,
            reason="the selected video is longer than the configured processing limit",
            clarification_prompt=(
                f"{selection.title} is long. Do you want Netra to analyse all of it?"
            ),
        )
    return ProcessingGate(
        decision=ProcessingDecision.PROCEED,
        reason="relevance is clear and no ingestion problem is known",
    )
