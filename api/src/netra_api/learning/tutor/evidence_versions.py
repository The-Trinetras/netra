"""Compare the source version a handoff declares with the one evidence resolved to.

INT-03 (docs/team/integration-playbook.md). Two representations of the
same identity meet at the Tutor:

- ``EvidenceRef.source_version_id`` in ``shared/contracts/agent/v1`` is a
  free-form JSON string (the committed contract example uses
  ``"src-ver-104"``);
- ``Evidence.source_version_id`` from M2's resolver is a ``UUID``.

Comparing them as raw strings would be "trusting mismatched
representations": ``str(UUID)`` is lowercase and hyphenated, so an
uppercase or braced spelling of the same UUID would read as a mismatch,
while a non-UUID declaration could never be checked at all. This module
compares canonical UUID values instead, and treats a declaration it
cannot interpret as *incomparable* rather than as a match.

What the Tutor does with each outcome (in ``agent._resolve_evidence``):

- MATCH: evidence is used.
- MISMATCH: evidence is dropped. The handoff was built against one source
  version and the resolver returned another; teaching from it would mix
  versions inside a pinned session (backend-data.md: "Reject inaccessible,
  deleted, stale or incompatible-version references").
- INCOMPARABLE: evidence is dropped. Fail closed: a version the Tutor
  cannot verify is not assumed to be the right one.

``EvidenceRef.evidence_version`` is NOT compared here: ``Evidence``
carries no evidence version, so the Tutor has nothing to compare it with
and relies on the resolver. That remaining half of INT-03 needs an M1/M2
decision on where evidence versions are represented; it is recorded in
docs/team/handoffs/M4.md rather than guessed.

The canonical representation of source versions on the wire is an M1/M2
decision. This module does not make it: it only refuses to treat two
values as equal unless both denote the same UUID.
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
    """The declared value is not a UUID, so equality cannot be established."""


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
    if declared is None:
        return SourceVersionComparison.INCOMPARABLE
    if declared == evidence.source_version_id:
        return SourceVersionComparison.MATCH
    return SourceVersionComparison.MISMATCH
