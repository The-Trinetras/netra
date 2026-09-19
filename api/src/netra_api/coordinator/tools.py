"""Coordinator tool adapters over teammates' reviewed service interfaces.

Each adapter converts a strict, model-supplied argument model plus trusted
ToolContext into calls on M2/M3 services, then re-validates what comes back
through the evidence authorization boundary before returning ToolEvidence:

- search_sources  -> M2 RetrievalService.search + EvidenceResolver.resolve
                     (pinned to the session's source version; vector hits are
                     never trusted until PostgreSQL-backed resolution accepts them)
- describe_figure -> M3 FigureService.get_figure + authorize_figure_evidence
- search_lecture  -> M3 VideoEvidenceService.search_evidence + resolve_and_authorize

Register a tool only when its service implementation exists
(register_available_tools). Rejection reasons stay internal: the model sees a
count of unavailable candidates, never why.

Timeouts default to the full answer deadline and are always capped by the
remaining turn time in ToolGateway; no separate per-tool timeout policy is
invented here.
"""

from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from netra_api.content.retrieval.evidence import EvidenceTrust
from netra_api.content.retrieval.service import RetrievalQuery
from netra_api.coordinator.limits import ANSWER_DEADLINE_SECONDS
from netra_api.coordinator.tool_registry import (
    MAX_EVIDENCE_TEXT_CHARS,
    ToolContext,
    ToolDefinition,
    ToolEvidence,
    ToolObservation,
    ToolRegistry,
    ToolResult,
)
from netra_api.multimedia.evidence import ObservationSource, resolve_and_authorize
from netra_api.multimedia.figures.evidence import authorize_figure_evidence
from netra_api.multimedia.video.models import VideoEvidenceKind
from netra_api.multimedia.video.timestamps import format_timestamp
from netra_api.platform.awaitables import maybe_await
from netra_api.platform.errors import NetraError, ResourceUnavailableError


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SearchSourcesArgs(_Args):
    query: str = Field(min_length=1, max_length=500)
    top_k: int = Field(default=5, ge=1, le=8)


class DescribeFigureArgs(_Args):
    figure_index: int = Field(ge=0, le=10_000)


class SearchLectureArgs(_Args):
    query: str = Field(min_length=1, max_length=500)
    lecture_source_version_id: str = Field(min_length=1, max_length=64)
    start_ms: Optional[int] = Field(default=None, ge=0)
    end_ms: Optional[int] = Field(default=None, ge=0)


def _pinned_uuid(context: ToolContext) -> UUID:
    pinned = context.pinned_source_version_id
    if pinned is None:
        raise ResourceUnavailableError("no source is pinned")
    return UUID(pinned)


def _in_scope(context: ToolContext, source_version_id: str) -> bool:
    return source_version_id == context.pinned_source_version_id or source_version_id in context.companion_source_version_ids


class SearchSourcesTool:
    definition = ToolDefinition(
        name="search_sources",
        description="Search the student's pinned source for passages, table text and equations.",
        input_model=SearchSourcesArgs,
        timeout_seconds=ANSWER_DEADLINE_SECONDS,
    )

    def __init__(self, retrieval: Any, resolver: Any) -> None:
        self._retrieval = retrieval
        self._resolver = resolver

    async def __call__(self, context: ToolContext, arguments: SearchSourcesArgs) -> ToolResult:
        pinned = _pinned_uuid(context)
        hits = await maybe_await(
            self._retrieval.search(
                context.auth,
                RetrievalQuery(query_text=arguments.query, source_version_ids=[pinned], top_k=arguments.top_k),
            )
        )
        ids = list(dict.fromkeys(hit.evidence_id for hit in hits))[: arguments.top_k]
        if not ids:
            return ToolResult()
        resolutions = await maybe_await(self._resolver.resolve(context.auth, ids, pinned_source_version_id=pinned))
        evidence = []
        rejected = 0
        for resolution in resolutions:
            item = resolution.evidence
            if item is None or str(item.source_version_id) != str(pinned):
                rejected += 1
                continue
            evidence.append(
                ToolEvidence(
                    evidence_id=item.evidence_id,
                    source_version_id=str(item.source_version_id),
                    evidence_version=item.evidence_version,
                    locator=item.locator[:500],
                    text=item.text[:MAX_EVIDENCE_TEXT_CHARS],
                    provenance=item.provenance[:200],
                    trust=item.trust,
                )
            )
        rejected += max(0, len(ids) - len(resolutions))
        return ToolResult(evidence=tuple(evidence), rejected_count=rejected)


class DescribeFigureTool:
    definition = ToolDefinition(
        name="describe_figure",
        description="Get the structured description of a figure or graph in the pinned source by its index, "
        "including axis and label observations marked observed, estimated, generated or unreadable.",
        input_model=DescribeFigureArgs,
        timeout_seconds=ANSWER_DEADLINE_SECONDS,
    )

    def __init__(self, figures: Any, resolver: Any) -> None:
        self._figures = figures
        self._resolver = resolver

    async def __call__(self, context: ToolContext, arguments: DescribeFigureArgs) -> ToolResult:
        pinned = _pinned_uuid(context)
        figure = await maybe_await(self._figures.get_figure(context.auth, pinned, arguments.figure_index))
        try:
            authorized = await authorize_figure_evidence(context.auth, self._resolver, figure.reference)
        except NetraError:
            return ToolResult(rejected_count=1)
        if str(authorized.source_version_id) != str(pinned):
            return ToolResult(rejected_count=1)

        observations = []
        for region in figure.regions[:12]:
            label = (region.label or f"region {region.ordinal + 1}")[:200]
            observations.append(ToolObservation(label=label, value=region.label, source=region.label_source))
            observations.append(
                ToolObservation(label=f"{label} description", value=region.description[:1000], source=region.description_source)
            )
        text = f"{figure.short_label}. {figure.long_description}"[:MAX_EVIDENCE_TEXT_CHARS]
        return ToolResult(
            evidence=(
                ToolEvidence(
                    evidence_id=authorized.evidence_id,
                    source_version_id=str(authorized.source_version_id),
                    evidence_version=authorized.evidence_version,
                    locator=authorized.locator[:500],
                    text=text,
                    provenance=authorized.provenance[:200],
                    trust=EvidenceTrust.DERIVED,
                    observations=tuple(observations[:24]),
                ),
            )
        )


class SearchLectureTool:
    definition = ToolDefinition(
        name="search_lecture",
        description="Search processed evidence (transcript and visual descriptions) of a lecture video in scope, "
        "optionally within a time window in milliseconds.",
        input_model=SearchLectureArgs,
        timeout_seconds=ANSWER_DEADLINE_SECONDS,
        requires_pinned_source=False,
    )

    def __init__(self, video: Any, resolver: Any) -> None:
        self._video = video
        self._resolver = resolver

    async def __call__(self, context: ToolContext, arguments: SearchLectureArgs) -> ToolResult:
        if not _in_scope(context, arguments.lecture_source_version_id):
            return ToolResult(rejected_count=1)
        try:
            version = UUID(arguments.lecture_source_version_id)
        except ValueError:
            return ToolResult(rejected_count=1)
        items = await maybe_await(self._video.search_evidence(context.auth, version, arguments.query))

        evidence = []
        rejected = 0
        for item in items[:12]:
            reference = item.reference
            if arguments.start_ms is not None and reference.end_ms < arguments.start_ms:
                continue
            if arguments.end_ms is not None and reference.start_ms > arguments.end_ms:
                continue
            try:
                authorized = await resolve_and_authorize(context.auth, self._resolver, reference)
            except NetraError:
                rejected += 1
                continue
            # M3's VideoEvidenceKind.supports_visual_claim: only a visual
            # description can back a claim about what is SHOWN, and it is
            # model-produced (GENERATED), so the ledger will not accept it as a
            # source observation. A transcript is observed SPEECH; its label
            # says so, and it is never labelled as a visual observation.
            # Accepting visual descriptions as support is an open M1/M3 rule.
            if item.kind == VideoEvidenceKind.TRANSCRIPT_SEGMENT:
                label, source = "spoken transcript (not visual evidence)", ObservationSource.OBSERVED
            else:
                label, source = f"{item.kind.value} (model description)", ObservationSource.GENERATED
            evidence.append(
                ToolEvidence(
                    evidence_id=authorized.evidence_id,
                    source_version_id=str(authorized.source_version_id),
                    evidence_version=authorized.evidence_version,
                    locator=f"{reference.locator} {format_timestamp(reference.start_ms)}-{format_timestamp(reference.end_ms)}"[:500],
                    text=item.description[:MAX_EVIDENCE_TEXT_CHARS],
                    provenance=authorized.provenance[:200],
                    trust=EvidenceTrust.DERIVED,
                    observations=(ToolObservation(label=label, value=None, source=source),),
                )
            )
        return ToolResult(evidence=tuple(evidence), rejected_count=rejected)


def register_available_tools(
    registry: ToolRegistry,
    *,
    resolver: Any = None,
    retrieval: Any = None,
    figures: Any = None,
    video: Any = None,
) -> list[str]:
    """Register only tools whose backing services are actually supplied."""

    registered = []
    if resolver is None:
        return registered
    if retrieval is not None:
        registry.register(SearchSourcesTool.definition, SearchSourcesTool(retrieval, resolver))
        registered.append("search_sources")
    if figures is not None:
        registry.register(DescribeFigureTool.definition, DescribeFigureTool(figures, resolver))
        registered.append("describe_figure")
    if video is not None:
        registry.register(SearchLectureTool.definition, SearchLectureTool(video, resolver))
        registered.append("search_lecture")
    return registered
