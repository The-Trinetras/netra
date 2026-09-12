from datetime import datetime, timezone
from uuid import uuid4

import pytest

from netra_api.content.retrieval.evidence import (
    Evidence,
    EvidenceRejectionReason,
    EvidenceResolution,
    EvidenceTrust,
)
from netra_api.multimedia.evidence import (
    UnauthorizedEvidenceReferenceError,
    VisualEvidenceReference,
    resolve_and_authorize,
)
from netra_api.platform.auth_context import AuthContext


class FakeResolver:
    """Structurally implements netra_api.content.retrieval.evidence.EvidenceResolver.

    Returns one EvidenceResolution per requested id, in order, the way a
    real resolver must.
    """

    def __init__(self, evidence_by_id):
        self._evidence_by_id = evidence_by_id

    def resolve(self, auth, evidence_ids, pinned_source_version_id=None):
        resolutions = []
        for evidence_id in evidence_ids:
            evidence = self._evidence_by_id.get(evidence_id)
            if evidence is None:
                resolutions.append(
                    EvidenceResolution(
                        evidence_id=evidence_id,
                        rejection_reason=EvidenceRejectionReason.NOT_FOUND,
                    )
                )
                continue
            if (
                pinned_source_version_id is not None
                and evidence.source_version_id != pinned_source_version_id
            ):
                resolutions.append(
                    EvidenceResolution(
                        evidence_id=evidence_id,
                        rejection_reason=EvidenceRejectionReason.SOURCE_VERSION_MISMATCH,
                    )
                )
                continue
            resolutions.append(
                EvidenceResolution(evidence_id=evidence_id, evidence=evidence)
            )
        return resolutions


def _auth():
    return AuthContext(
        account_id=uuid4(), session_id=uuid4(), request_id=uuid4(), issued_at=datetime.now(timezone.utc)
    )


def test_resolve_and_authorize_accepts_derived_evidence_for_matching_version():
    source_version_id = uuid4()
    evidence = Evidence(
        evidence_id="ev-1",
        source_version_id=source_version_id,
        locator="figure-1",
        text="A bar chart showing rainfall by month.",
        provenance="figure_processing",
        trust=EvidenceTrust.DERIVED,
    )
    resolver = FakeResolver({"ev-1": evidence})
    reference = VisualEvidenceReference(
        evidence_id="ev-1", source_version_id=source_version_id, locator="figure-1"
    )

    resolved = resolve_and_authorize(_auth(), resolver, reference)

    assert resolved == evidence


def test_resolve_and_authorize_rejects_unresolved_evidence_id():
    resolver = FakeResolver({})
    reference = VisualEvidenceReference(
        evidence_id="ev-missing", source_version_id=uuid4(), locator="figure-1"
    )

    with pytest.raises(UnauthorizedEvidenceReferenceError):
        resolve_and_authorize(_auth(), resolver, reference)


def test_resolve_and_authorize_rejects_non_derived_trust():
    source_version_id = uuid4()
    evidence = Evidence(
        evidence_id="ev-2",
        source_version_id=source_version_id,
        locator="p-3",
        text="Directly quoted source text.",
        provenance="reading_block",
        trust=EvidenceTrust.SOURCE_VERIFIED,
    )
    resolver = FakeResolver({"ev-2": evidence})
    reference = VisualEvidenceReference(
        evidence_id="ev-2", source_version_id=source_version_id, locator="p-3"
    )

    with pytest.raises(UnauthorizedEvidenceReferenceError):
        resolve_and_authorize(_auth(), resolver, reference)


def test_resolve_and_authorize_rejects_source_version_mismatch():
    evidence = Evidence(
        evidence_id="ev-3",
        source_version_id=uuid4(),
        locator="figure-2",
        text="A described figure.",
        provenance="figure_processing",
        trust=EvidenceTrust.DERIVED,
    )
    resolver = FakeResolver({"ev-3": evidence})
    reference = VisualEvidenceReference(
        evidence_id="ev-3", source_version_id=uuid4(), locator="figure-2"
    )

    with pytest.raises(UnauthorizedEvidenceReferenceError):
        resolve_and_authorize(_auth(), resolver, reference)
