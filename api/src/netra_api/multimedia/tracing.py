"""Media spans through M1's tracing boundary.

docs/architecture/arize-ax-integration.md makes M3 responsible for
"media retrieval/analysis spans and evidence provenance". Every span here
goes through netra_api.platform.tracing.Tracer, so M1's allowlist decides
what may leave the process. This module adds no exporter, no SDK type and
no second tracing stack.

What is recorded: operation, provider/model pins, attempt number, source
version, outcome and safe error codes, evidence ids and counts, and the
uncertainty class of the result (observed / generated / transcript-only /
unreadable). What is never recorded: media bytes, URLs (signed or not),
query text, transcript or description text, provider payloads and
exception messages. Those have no allowlisted key, and this module never
tries to pass them.

Time ranges and the captured player time are the one gap. M1's allowlist
has no media-time keys yet. They are listed in PROPOSED_MEDIA_ATTRIBUTES
with their validators and are emitted only when M1's ALLOWED_ATTRIBUTES
already contains them, so approving the keys in M1's module switches them
on without an M3 change, and until then nothing is silently dropped or
counted as a sanitizer rejection. Recorded as M3-TR-1 in
docs/team/handoffs/M3.md.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, Optional

from netra_api.platform.tracing import ALLOWED_ATTRIBUTES, DISABLED_TRACER, Tracer

PROPOSED_MEDIA_ATTRIBUTES: dict[str, str] = {
    "netra.media.video_id": "opaque id: canonical VideoAsset.video_id, never a provider asset id",
    "netra.media.start_ms": "int >= 0: evidence/query window start",
    "netra.media.end_ms": "int >= start_ms: evidence/query window end",
    "netra.media.captured_time_ms": "int >= 0: M5's captured player position",
    "netra.media.stage": "code: job stage name (index_video, derive_video_evidence, extract_object)",
    "netra.media.object_kind": "code: figure/chart/diagram/equation/table/video",
    "netra.media.uncertainty": "code: observed/generated/estimated/unreadable/transcript_only/not_checked",
}
"""Keys M3 asks M1 to add to ALLOWED_ATTRIBUTES. Not approved; see M3-TR-1."""


def _proposed_supported(key: str) -> bool:
    return key in ALLOWED_ATTRIBUTES


def media_attributes(
    *,
    operation: str,
    provider: Optional[str] = None,
    model_name: Optional[str] = None,
    attempt: Optional[int] = None,
    source_version_id: Optional[object] = None,
    video_id: Optional[object] = None,
    start_ms: Optional[int] = None,
    end_ms: Optional[int] = None,
    captured_time_ms: Optional[int] = None,
    stage: Optional[str] = None,
    object_kind: Optional[str] = None,
    uncertainty: Optional[str] = None,
) -> dict[str, Any]:
    """Build the attribute mapping for one media span.

    Uses existing M1 keys wherever one fits; proposed media keys are
    included only once M1 has allowlisted them.
    """

    attributes: dict[str, Any] = {"netra.operation": operation}
    if provider is not None:
        attributes["llm.provider"] = provider
    if model_name is not None:
        attributes["llm.model_name"] = model_name
    if attempt is not None:
        attributes["netra.attempt"] = attempt
    if source_version_id is not None:
        attributes["netra.source_version_id"] = str(source_version_id)

    proposed = {
        "netra.media.video_id": str(video_id) if video_id is not None else None,
        "netra.media.start_ms": start_ms,
        "netra.media.end_ms": end_ms,
        "netra.media.captured_time_ms": captured_time_ms,
        "netra.media.stage": stage,
        "netra.media.object_kind": object_kind,
        "netra.media.uncertainty": uncertainty,
    }
    for key, value in proposed.items():
        if value is not None and _proposed_supported(key):
            attributes[key] = value
    return attributes


@contextmanager
def media_span(tracer: Optional[Tracer], name: str, **fields: Any) -> Iterator[Any]:
    """One media operation span; a missing tracer means tracing is off.

    Tracing never changes control flow: exceptions propagate unchanged,
    and the span is ended on success, error, timeout and cancellation by
    M1's Tracer.span.
    """

    active = tracer if tracer is not None else DISABLED_TRACER
    with active.span(name, **media_attributes(**fields)) as span:
        yield span


def record_outcome(span: Any, outcome: str, *, uncertainty: Optional[str] = None, **facts: Any) -> None:
    """Record a closed outcome code plus optional uncertainty on a span.

    ``facts`` accepts only already-safe values (counts, ids, codes); the
    M1 sanitizer still drops anything that is not allowlisted.
    """

    attributes: dict[str, Any] = {"netra.outcome": outcome}
    if uncertainty is not None:
        if _proposed_supported("netra.media.uncertainty"):
            attributes["netra.media.uncertainty"] = uncertainty
        else:
            # netra.gap is an existing allowlisted code key; an uncertain
            # result is recorded as the gap it represents.
            attributes["netra.gap"] = uncertainty
    attributes.update(facts)
    span.set(**attributes)
