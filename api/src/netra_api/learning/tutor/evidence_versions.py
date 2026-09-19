"""Compare the evidence identity a handoff declares with what the resolver returns.

INT-03 (docs/team/integration-playbook.md). Two representations of the
same identity meet at the Tutor:

- ``EvidenceRef.source_version_id`` in ``shared/contracts/agent/v1``: the
  canonical UUID string of the immutable source version (the contract
  declares ``format: uuid``; the Python mirror in
  netra_api.coordinator.handoff normalizes and enforces it), and
  ``EvidenceRef.evidence_version``: the immutable version of that evidence
  content;
- ``Evidence.source_version_id`` (a ``UUID``) and
  ``Evidence.evidence_version`` (the owning source version's
  ``version_number``) from M2's resolver.

Comparing source versions as raw strings would be "trusting mismatched
representations", so canonical UUID values are compared. A declaration the
Tutor cannot interpret, or resolved evidence that carries no evidence
version, is *incomparable* rather than a match.

What the Tutor does with each outcome (in ``agent._resolve_evidence``):

- MATCH: evidence is used.
- MISMATCH: evidence is dropped. The handoff was built against one source
  or evidence version and the resolver returned another; teaching from it
  would mix versions inside a pinned session (backend-data.md: "Reject
  inaccessible, deleted, stale or incompatible-version references").
- INCOMPARABLE: evidence is dropped. Fail closed: a version the Tutor
  cannot verify is not assumed to be the right one. This remains a
  defence in depth: a contract-valid handoff can no longer carry a
  non-UUID source version.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional
from uuid import UUID

from netra_api.content.retrieval.evidence import Evidence
from netra_api.coordinator.handoff import EvidenceRef


class SourceVersionComparison(str, Enum):
    MATCH = "match"
    MISMATCH = "mismatch"
    INCOMPARABLE = "incomparable"
    """The declared source version is not a UUID, or the resolved evidence
    carries no evidence version, so equality cannot be established."""


def canonical_source_version(declared: str) -> Optional[UUID]:
    """Parse a declared source_version_id into its canonical UUID, or None.

    ``uuid.UUID`` accepts hyphenated, braced, ``urn:uuid:`` and bare-hex
    spellings in any case; each yields the same value, so formatting
    differences never become false mismatches.
    """

    try:
        return UUID(declared.strip())
    except (ValueError, AttributeError):
        return None


def compare_source_version(ref: EvidenceRef, evidence: Evidence) -> SourceVersionComparison:
    declared = canonical_source_version(ref.source_version_id)
    if declared is None or evidence.evidence_version is None:
        return SourceVersionComparison.INCOMPARABLE
    if declared != evidence.source_version_id or ref.evidence_version != evidence.evidence_version:
        return SourceVersionComparison.MISMATCH
    return SourceVersionComparison.MATCH
