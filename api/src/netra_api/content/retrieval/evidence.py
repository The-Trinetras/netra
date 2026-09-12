"""Evidence resolution interface.

CLAUDE.md "Evidence rules": models may reference evidence IDs but must
not construct authoritative evidence text themselves; the server
resolves IDs to authoritative stored content, and every evidence item
keeps its source version, locator, provenance, and trust
classification. This module is that resolution boundary — it is also
where a RetrievalHit.evidence_id (see
netra_api.content.retrieval.service) gets authorized against
PostgreSQL before reaching model context.
"""

from __future__ import annotations

from enum import Enum
from typing import List, Optional, Protocol
from uuid import UUID

from pydantic import BaseModel

from netra_api.platform.auth_context import AuthContext


class EvidenceTrust(str, Enum):
    """How much a piece of evidence can be relied on without further checking."""

    SOURCE_VERIFIED = "source_verified"
    """Resolved directly from an authoritative SourceVersion's ReadingBlocks."""
    DERIVED = "derived"
    """Produced by a bounded tool (e.g. a figure/equation description) from a verified source."""


class Evidence(BaseModel):
    """Authoritative, resolved content the Coordinator/Tutor may cite."""

    evidence_id: str
    source_version_id: UUID
    locator: str
    text: str
    provenance: str
    trust: EvidenceTrust


class EvidenceRejectionReason(str, Enum):
    """Why one evidence_id did not resolve.

    Internal diagnostics only. These values distinguish an empty result
    from a denied one for logging, reduced-mode decisions and tests
    (backend-data.md: "Distinguish empty results, unavailable
    dependencies and denied access"). Never echo them to a student or
    into model context: telling an unauthorized caller the difference
    between "no such evidence" and "not yours" turns the resolver into an
    existence oracle for other accounts' content.
    """

    NOT_FOUND = "not_found"
    UNAUTHORIZED = "unauthorized"
    DELETED = "deleted"
    SOURCE_VERSION_MISMATCH = "source_version_mismatch"


class EvidenceResolution(BaseModel):
    """The outcome of resolving one evidence_id: either evidence or a reason."""

    evidence_id: str
    evidence: Optional[Evidence] = None
    rejection_reason: Optional[EvidenceRejectionReason] = None

    @property
    def is_resolved(self) -> bool:
        return self.evidence is not None


class EvidenceResolver(Protocol):
    """Typed contract for turning evidence IDs into authoritative Evidence.

    Implementations must authorize every evidence_id against auth's
    account before returning it (CLAUDE.md: "Validate vector references
    against PostgreSQL before supplying evidence to models. Reject
    unauthorized, deleted, stale or source-version-incompatible
    references").

    One resolution is returned per requested id, in the order requested.
    Returning an outcome per id rather than a filtered list of hits is
    what lets a caller tell "this evidence does not exist" apart from
    "this evidence belongs to someone else" apart from "this evidence is
    from the wrong version of the document", while still allowing a
    partial context to be built from whatever did resolve.
    """

    def resolve(
        self,
        auth: AuthContext,
        evidence_ids: List[str],
        pinned_source_version_id: Optional[UUID] = None,
    ) -> List[EvidenceResolution]:
        """Resolve and authorize evidence ids.

        When pinned_source_version_id is supplied, evidence belonging to
        any other source version is rejected with
        SOURCE_VERSION_MISMATCH rather than returned. Sessions stay
        pinned to the source version they started on (CLAUDE.md: "Source
        sessions remain pinned to their source version until an explicit
        switch"), so evidence retrieved from a newly activated version
        must not silently enter a pinned session's context.
        """
        ...


def resolved_evidence(resolutions: List[EvidenceResolution]) -> List[Evidence]:
    """Narrow resolutions to the evidence that actually resolved."""

    return [
        resolution.evidence for resolution in resolutions if resolution.evidence is not None
    ]
