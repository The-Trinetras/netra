"""Figure evidence-reference model and validation boundary.

CLAUDE.md "Evidence rules": every evidence item keeps its source
version, locator, provenance, and trust classification. This module
fixes FigureEvidenceReference — the anchor between a
netra_api.multimedia.figures.models.FigureDescription and its source
figure — and authorize_figure_evidence, the call figures/service.py
must make before citing one to the Coordinator/Tutor. Delegates to the
shared boundary in netra_api.multimedia.evidence rather than
duplicating authorization logic per multimedia subsystem.
"""

from __future__ import annotations

from pydantic import Field

from netra_api.content.retrieval.evidence import Evidence, EvidenceResolver
from netra_api.multimedia.evidence import VisualEvidenceReference, resolve_and_authorize
from netra_api.platform.auth_context import AuthContext


class FigureEvidenceReference(VisualEvidenceReference):
    """Anchors a FigureDescription to its source figure's locator."""

    figure_index: int = Field(ge=0)
    """Ordinal of this figure within source_version_id, for stable re-reference."""


async def authorize_figure_evidence(
    auth: AuthContext, resolver: EvidenceResolver, reference: FigureEvidenceReference
) -> Evidence:
    """Validate one figure's evidence reference before it may be cited.

    Raises netra_api.multimedia.evidence.UnauthorizedEvidenceReferenceError
    if reference is not an authorized, DERIVED-trust reference to
    reference.source_version_id.
    """

    return await resolve_and_authorize(auth, resolver, reference)
