"""Select stored video evidence with Marengo retrieval intervals.

Marengo (MarengoSearchAdapter) returns *intervals*, not evidence. What a
student is told must come from registered, authorized evidence items, so
a search maps each retrieval interval onto the stored items that overlap
it. Items keep their own time ranges and text; the provider's ranking
only orders them. An item no interval touches is not returned, and a hit
that touches no stored item selects nothing — search never fabricates a
moment that was not processed.
"""

from __future__ import annotations

from typing import List, Sequence

from netra_api.multimedia.providers.twelve_labs_client import MarengoHit
from netra_api.multimedia.video.models import VideoEvidenceItem


def _overlaps(item: VideoEvidenceItem, hit: MarengoHit) -> bool:
    reference = item.reference
    return reference.start_ms <= hit.end_ms and hit.start_ms <= reference.end_ms


def select_evidence_for_hits(
    items: Sequence[VideoEvidenceItem], hits: Sequence[MarengoHit], *, limit: int
) -> List[VideoEvidenceItem]:
    """Items overlapping any hit, best-ranked hit first, each item once."""

    if limit <= 0:
        return []
    selected: List[VideoEvidenceItem] = []
    seen: set = set()
    for hit in hits:
        overlapping = sorted(
            (item for item in items if _overlaps(item, hit)),
            key=lambda item: (item.reference.start_ms, item.kind.value),
        )
        for item in overlapping:
            if item.video_evidence_id in seen:
                continue
            seen.add(item.video_evidence_id)
            selected.append(item)
            if len(selected) >= limit:
                return selected
    return selected
