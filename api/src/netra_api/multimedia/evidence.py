"""Shared evidence-reference boundary for multimedia-derived content.

CLAUDE.md "Evidence rules": models may reference evidence IDs but must
not construct authoritative evidence text themselves, and every
evidence item keeps its source version, locator, provenance, and trust
classification. Figure, diagram, equation, and video processing are
explicitly NOT agents (CLAUDE.md "Architecture: only two agents") —
they are bounded tools that produce *derived* evidence from an already
active, authorized SourceVersion. This module is the boundary every
multimedia bounded tool's output must pass through before a Coordinator
or Tutor tool call can cite it: it never resolves evidence text itself
and never opens a database connection (CLAUDE.md "Agents never own
database connections") — it only validates shape and delegates
authorization to netra_api.content.retrieval.evidence.EvidenceResolver.
"""

from __future__ import annotations

from enum import Enum
from uuid import UUID

from pydantic import BaseModel

from netra_api.content.retrieval.evidence import Evidence, EvidenceResolver, EvidenceTrust
from netra_api.platform.auth_context import AuthContext
from netra_api.platform.awaitables import maybe_await
from netra_api.platform.errors import NetraError


class ObservationSource(str, Enum):
    """Whether a detail was read off the source or produced by a model.

    multimedia.md: "Separate directly observed structure from generated
    interpretation" and "Mark estimated values and unreadable labels
    explicitly."

    This distinction cannot be recovered later. A blind student deciding
    whether to trust an axis value, and a Tutor deciding whether to cite
    one as fact, both need to know the difference between a label that
    was actually legible in the figure and a plausible reconstruction of
    one. Collapsing them is how a model's guess becomes an authoritative
    measurement.
    """

    OBSERVED = "observed"
    """Read directly from the source: legible text, explicit structure."""
    GENERATED = "generated"
    """Produced by a model interpreting the source. Never a measurement."""
    ESTIMATED = "estimated"
    """Inferred from the source but not stated by it, such as a value read
    off an unlabelled axis position. Must be presented as approximate."""
    UNREADABLE = "unreadable"
    """Present in the source but could not be read. Explicitly not a
    missing detail, and never to be filled in by guessing."""


class VisualEvidenceReference(BaseModel):
    """Base shape for evidence a multimedia bounded tool derives from a source.

    Every subtype (figure/diagram/equation/video) carries an evidence_id
    that must resolve through EvidenceResolver, never inline
    authoritative text — the derived description itself is untrusted
    provider/model output until it is registered as Evidence with trust
    DERIVED (see netra_api.content.retrieval.evidence.EvidenceTrust).
    """

    evidence_id: str
    source_version_id: UUID
    locator: str
    """Page/region/timestamp locator within source_version_id, matching
    the conventions of netra_api.content.reading.blocks.ReadingBlock.locator."""


class UnauthorizedEvidenceReferenceError(NetraError):
    """Raised when a multimedia evidence reference fails the validation boundary.

    Covers three cases uniformly: the evidence_id does not resolve/is
    not authorized for the caller's account, it resolves to a
    source_version_id other than the one the reference claims, or it
    resolves with a trust level other than DERIVED. A multimedia
    bounded tool must never claim SOURCE_VERIFIED trust for content it
    produced itself.
    """

    def __init__(self, evidence_id: str) -> None:
        self.evidence_id = evidence_id
        super().__init__(f"evidence_id {evidence_id} is not an authorized, derived reference")


async def resolve_and_authorize(
    auth: AuthContext, resolver: EvidenceResolver, reference: VisualEvidenceReference
) -> Evidence:
    """Resolve reference.evidence_id and enforce it is DERIVED trust for the right version.

    This is the evidence validation boundary every multimedia bounded
    tool must call before citing its own output (CLAUDE.md "Models may
    reference evidence IDs. They must not construct authoritative
    evidence text themselves.").

    EvidenceResolver.resolve authorizes evidence_id against
    auth.account_id and, given the reference's source_version_id as the
    pin, rejects evidence from any other version. This function then
    rejects anything that is not DERIVED trust, and re-checks the
    source version on the returned record rather than assuming the
    resolver honoured the pin.

    All failures collapse to one exception on purpose: a caller learns
    that a reference is unusable, not why. The specific
    EvidenceRejectionReason stays internal to the resolver.
    """

    resolutions = await maybe_await(
        resolver.resolve(
            auth,
            [reference.evidence_id],
            pinned_source_version_id=reference.source_version_id,
        )
    )
    if not resolutions:
        raise UnauthorizedEvidenceReferenceError(reference.evidence_id)

    evidence = resolutions[0].evidence
    if evidence is None:
        raise UnauthorizedEvidenceReferenceError(reference.evidence_id)
    if evidence.trust != EvidenceTrust.DERIVED:
        raise UnauthorizedEvidenceReferenceError(reference.evidence_id)
    if evidence.source_version_id != reference.source_version_id:
        raise UnauthorizedEvidenceReferenceError(reference.evidence_id)

    return evidence
