"""Figure domain models — structured, navigable descriptions for blind users.

Figure processing is an explicitly-listed bounded tool, not an agent
(CLAUDE.md "Architecture: only two agents"). A FigureDescription
decomposes a source figure into a short label, a full narrative
description, and an ordered list of described regions/callouts, so a
screen-reader client can present a figure the way a sighted reader
would scan it: overview first, detail on request. Always anchored to
source evidence via FigureEvidenceReference — never presented as
authoritative on its own (CLAUDE.md "Evidence rules"); see
netra_api.multimedia.figures.evidence.authorize_figure_evidence.
"""

from __future__ import annotations

from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from netra_api.multimedia.evidence import ObservationSource
from netra_api.multimedia.figures.evidence import FigureEvidenceReference


class FigureRegion(BaseModel):
    """One described sub-part of a figure (a callout, subplot, legend entry, ...)."""

    ordinal: int = Field(ge=0)
    label: Optional[str] = None
    label_source: ObservationSource = ObservationSource.OBSERVED
    """Whether label was legible in the figure or reconstructed. A region
    whose label could not be read must say UNREADABLE rather than carry a
    plausible guess (multimedia.md: "Never invent a label, relationship,
    coordinate or measurement")."""
    description: str
    description_source: ObservationSource = ObservationSource.GENERATED
    """Descriptions are model prose by default. Only mark OBSERVED when the
    text was taken verbatim from the source."""


class FigureDescription(BaseModel):
    """A fully structured, screen-reader-navigable description of one figure."""

    figure_id: UUID
    reference: FigureEvidenceReference
    short_label: str
    """Brief alt-text-equivalent summary, read first."""
    long_description: str
    """Full narrative description of the figure's content."""
    regions: List[FigureRegion] = Field(default_factory=list)
