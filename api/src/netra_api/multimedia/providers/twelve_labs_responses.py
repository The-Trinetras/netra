"""Validate Twelve Labs responses before anything treats them as evidence.

The adapter Protocols in netra_api.multimedia.providers.twelve_labs fix
what Marengo and Pegasus are asked for. This module fixes what may come
back. It is the point where provider output stops being a payload and
becomes, or fails to become, a candidate piece of evidence.

Two rules drive every check here:

- multimedia.md: "Do not publish an incomplete or failed asset as
  validated evidence." A description whose time range runs backwards, or
  extends past the end of the media, cannot be cited — the citation *is*
  the timestamp, so a wrong one sends the student to the wrong moment
  and looks authoritative doing it.
- CLAUDE.md: provider output is untrusted data. Pegasus generates text
  from a video; if that video contains a slide reading "ignore previous
  instructions", the words arrive here like any others. They are content
  to validate and store, never instructions, which is why nothing in
  this module interprets the text it checks.

No SDK is imported and no call is made. The inputs are the Netra-owned
result types the adapter has already converted provider objects into.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from netra_api.multimedia.providers.errors import MalformedProviderResponseError
from netra_api.multimedia.providers.twelve_labs import (
    MarengoEmbedding,
    PegasusGenerationKind,
    PegasusGenerationResult,
)
from netra_api.multimedia.video.models import (
    EvidenceProvenance,
    VideoAsset,
    VideoEvidenceCandidate,
    VideoEvidenceKind,
)

TWELVE_LABS_PROVIDER = "twelvelabs"
"""Adapter name recorded in provenance. Kept as a constant so evidence
produced by this adapter is queryable without matching on prose."""

MAX_DESCRIPTION_CHARS = 8000
"""Upper bound on one stored description.

Bounded because it is read aloud and stored per time range, and because
an unbounded provider string is an unbounded row. The value is a local
adapter guard, not a product policy: nothing in the approved scope sets
a description length, so this caps the obviously unusable rather than
deciding what a good description is."""


_KIND_BY_GENERATION: dict[PegasusGenerationKind, VideoEvidenceKind] = {
    PegasusGenerationKind.SCENE_DESCRIPTION: VideoEvidenceKind.VISUAL_DESCRIPTION,
    PegasusGenerationKind.SUMMARY: VideoEvidenceKind.SCENE_SUMMARY,
}
"""Which evidence kind each Pegasus generation may become.

OPEN_ENDED_QA is deliberately absent. An answer to a question is not a
description of a moment: it is shaped by the prompt, may draw on the
whole video, and would enter the store as evidence that appears to
describe a time range it never established. Netra asks Pegasus to
describe scenes and summarise; a question's answer is produced in a turn
and cited from the evidence that supported it, not stored as evidence
itself."""


def validate_pegasus_result(
    result: PegasusGenerationResult,
    *,
    kind: PegasusGenerationKind,
    duration_ms: Optional[int] = None,
) -> None:
    """Raise MalformedProviderResponseError unless result is usable as evidence.

    duration_ms is checked only when it is known. An unknown media
    duration is not treated as zero: that would reject every response for
    a video Netra has not measured, which is a different failure than the
    provider misbehaving.
    """

    text = result.text.strip()
    if not text:
        raise MalformedProviderResponseError(
            TWELVE_LABS_PROVIDER, "generation returned no text", field="text"
        )
    if len(text) > MAX_DESCRIPTION_CHARS:
        raise MalformedProviderResponseError(
            TWELVE_LABS_PROVIDER,
            f"generation text exceeds {MAX_DESCRIPTION_CHARS} characters",
            field="text",
        )

    if kind is PegasusGenerationKind.OPEN_ENDED_QA:
        # Valid to receive, but never convertible to stored evidence.
        return

    if result.start_ms is None or result.end_ms is None:
        raise MalformedProviderResponseError(
            TWELVE_LABS_PROVIDER,
            "a description must carry the time range it describes",
            field="start_ms",
        )
    if result.start_ms < 0:
        raise MalformedProviderResponseError(
            TWELVE_LABS_PROVIDER, "start_ms must not be negative", field="start_ms"
        )
    if result.end_ms < result.start_ms:
        raise MalformedProviderResponseError(
            TWELVE_LABS_PROVIDER, "end_ms precedes start_ms", field="end_ms"
        )
    if duration_ms is not None and result.end_ms > duration_ms:
        raise MalformedProviderResponseError(
            TWELVE_LABS_PROVIDER,
            f"end_ms {result.end_ms} is past the media duration {duration_ms}",
            field="end_ms",
        )


def pegasus_result_to_candidate(
    result: PegasusGenerationResult,
    *,
    asset: VideoAsset,
    kind: PegasusGenerationKind,
    locator: str,
    model_name: str,
    model_version: str,
    produced_at: datetime,
    stage: str,
) -> VideoEvidenceCandidate:
    """Convert one validated Pegasus result into a storable candidate.

    Validates first, so an unusable response cannot become a candidate
    even if a caller forgot to check. model_name/model_version are
    supplied by the adapter's configuration rather than read from the
    response, because the runtime baseline requires model ids to be
    "separate explicit configuration" — a provider echoing a model name
    is not the pin Netra agreed to use.
    """

    validate_pegasus_result(result, kind=kind, duration_ms=asset.duration_ms)

    evidence_kind = _KIND_BY_GENERATION.get(kind)
    if evidence_kind is None:
        raise MalformedProviderResponseError(
            TWELVE_LABS_PROVIDER,
            f"{kind.value} output is not stored as evidence",
            field="kind",
        )

    return VideoEvidenceCandidate(
        video_id=asset.video_id,
        source_version_id=asset.source_version_id,
        locator=locator,
        start_ms=int(result.start_ms),
        end_ms=int(result.end_ms),
        kind=evidence_kind,
        description=result.text.strip(),
        provenance=EvidenceProvenance(
            provider=TWELVE_LABS_PROVIDER,
            model_name=model_name,
            model_version=model_version,
            produced_at=produced_at,
            stage=stage,
        ),
    )


def validate_marengo_embeddings(
    embeddings: List[MarengoEmbedding],
    *,
    expected_dimensions: int,
    duration_ms: Optional[int] = None,
) -> None:
    """Raise unless every embedding is usable for search.

    expected_dimensions is required, not inferred from the first vector.
    backend-data.md: "Pin embedding model/version, dimensions and index
    configuration. Do not mix incompatible document/query embeddings."
    Inferring the dimension from whatever arrived is how an index quietly
    ends up holding two incompatible vector shapes.
    """

    if expected_dimensions <= 0:
        raise ValueError("expected_dimensions must be positive")

    if not embeddings:
        raise MalformedProviderResponseError(
            TWELVE_LABS_PROVIDER, "embedding response is empty", field="embeddings"
        )

    for ordinal, embedding in enumerate(embeddings):
        if len(embedding.vector) != expected_dimensions:
            raise MalformedProviderResponseError(
                TWELVE_LABS_PROVIDER,
                (
                    f"embedding {ordinal} has {len(embedding.vector)} dimensions, "
                    f"expected {expected_dimensions}"
                ),
                field="vector",
            )
        if embedding.start_ms is None and embedding.end_ms is None:
            continue
        if embedding.start_ms is None or embedding.end_ms is None:
            raise MalformedProviderResponseError(
                TWELVE_LABS_PROVIDER,
                f"embedding {ordinal} has only one end of its time range",
                field="start_ms",
            )
        if embedding.start_ms < 0 or embedding.end_ms < embedding.start_ms:
            raise MalformedProviderResponseError(
                TWELVE_LABS_PROVIDER,
                f"embedding {ordinal} has an invalid time range",
                field="end_ms",
            )
        if duration_ms is not None and embedding.end_ms > duration_ms:
            raise MalformedProviderResponseError(
                TWELVE_LABS_PROVIDER,
                f"embedding {ordinal} ends past the media duration {duration_ms}",
                field="end_ms",
            )


def provider_video_id_for(asset: VideoAsset, provider_video_id: str) -> UUID:
    """Assert that a provider id is never substituted for a canonical one.

    Returns the canonical video_id, ignoring provider_video_id entirely.
    It exists as a call site: code that has both ids in hand and needs
    "the" id gets the Netra one, and the provider id stays where it
    belongs, in ProviderAssetBinding (multimedia.md: "Keep provider
    asset/index IDs distinct from canonical Netra source IDs").
    """

    if not provider_video_id:
        raise MalformedProviderResponseError(
            TWELVE_LABS_PROVIDER, "provider returned an empty asset id", field="provider_video_id"
        )
    return asset.video_id
