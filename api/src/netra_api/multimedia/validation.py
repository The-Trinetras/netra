"""Source-check boundary shared by figure, table and equation extraction.

multimedia.md: "Successful syntax conversion does not prove the source
was read correctly" and "Unverified extraction must not become a
confident authoritative derivation." The AgentSpec asks the same
question first: compare the graph axes, three table rows and equation
against the originals, and record each mismatch.

This module is the mechanism for that comparison. It deliberately does
*not* try to read the original media: nothing in this repository can
look at a PDF crop and decide whether an exponent was transcribed
correctly. What it does is make the reviewer's act of checking
extraction against the original media a recorded, typed outcome that
extraction cannot fabricate for itself:

- a reviewer (or a fixture authored from the original media) supplies
  the expected values,
- the per-domain validators in netra_api.multimedia.figures.validation,
  netra_api.multimedia.tables.validation and
  netra_api.multimedia.equations.validation compare extraction against
  them field by field,
- the resulting ValidationReport is the only thing allowed to move
  extraction from "generated" to "checked against the source".

An extraction with no expectation is NOT_CHECKED, never MATCHES_SOURCE.
Fail-closed is the point: the default outcome of never having compared
anything is "not checked", so a missing review can never read as a pass.
"""

from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class SourceCheckStatus(str, Enum):
    """Outcome of comparing one extracted detail against the original media."""

    NOT_CHECKED = "not_checked"
    """No expectation was supplied for this detail. The default, and never
    an implicit pass."""
    MATCHES_SOURCE = "matches_source"
    """The extracted value equals the reviewer-recorded source value."""
    MISMATCH = "mismatch"
    """The extracted value contradicts the source: a wrong unit, a flipped
    sign, a lost exponent, a transposed cell."""
    UNREADABLE_IN_SOURCE = "unreadable_in_source"
    """The detail exists in the source but the reviewer could not read it
    either. Distinct from MISMATCH, because there is nothing to match
    against, and distinct from NOT_CHECKED, because somebody looked."""
    UNSUPPORTED = "unsupported"
    """Netra cannot currently extract this kind of detail at all. Reported
    explicitly rather than silently omitted (multimedia.md: "Return
    explicit unavailable/unsupported/uncertain outcomes through
    contracts")."""


class ValidationFinding(BaseModel):
    """One compared detail: what was expected, what was extracted, the verdict."""

    part_id: str
    """Stable ID of the compared part (axis, cell, equation node), so a
    finding can be pointed at without depending on regenerated prose."""
    field: str
    """Which attribute of that part was compared, e.g. "unit" or "value"."""
    status: SourceCheckStatus
    expected: Optional[str] = None
    """The reviewer-recorded source value, rendered as text. None when the
    status is NOT_CHECKED or UNSUPPORTED."""
    extracted: Optional[str] = None
    """What extraction produced, rendered as text. None when extraction
    produced nothing for this field."""
    detail: Optional[str] = None
    """Short operational explanation. Never private model reasoning."""


class ValidationReport(BaseModel):
    """Every finding for one extracted object, plus its overall verdict.

    is_source_verified is the gate the rest of the system reads: only an
    object whose every checked detail matched, with at least one detail
    actually checked, may be presented as validated against the source.
    """

    object_id: str
    findings: List[ValidationFinding] = Field(default_factory=list)

    @property
    def checked_findings(self) -> List[ValidationFinding]:
        return [f for f in self.findings if f.status is not SourceCheckStatus.NOT_CHECKED]

    @property
    def mismatches(self) -> List[ValidationFinding]:
        return [f for f in self.findings if f.status is SourceCheckStatus.MISMATCH]

    @property
    def unreadable(self) -> List[ValidationFinding]:
        return [f for f in self.findings if f.status is SourceCheckStatus.UNREADABLE_IN_SOURCE]

    @property
    def unsupported(self) -> List[ValidationFinding]:
        return [f for f in self.findings if f.status is SourceCheckStatus.UNSUPPORTED]

    @property
    def is_source_verified(self) -> bool:
        """True only when something was checked and nothing failed.

        An empty report is not a pass. A report containing an
        UNREADABLE_IN_SOURCE finding is not a pass either: "the reviewer
        could not read it" is a stated limitation, which is exactly the
        case the AgentSpec requires to end in a stated gap rather than a
        confident comparison.
        """

        if not self.checked_findings:
            return False
        return all(f.status is SourceCheckStatus.MATCHES_SOURCE for f in self.checked_findings)

    def summary(self) -> str:
        """One-line operational summary for a job or tool action record."""

        return (
            f"{self.object_id}: {len(self.checked_findings)} checked, "
            f"{len(self.mismatches)} mismatched, {len(self.unreadable)} unreadable, "
            f"{len(self.unsupported)} unsupported"
        )


def finding(
    part_id: str,
    field: str,
    expected: Optional[object],
    extracted: Optional[object],
    *,
    unreadable_in_source: bool = False,
    unsupported: bool = False,
    detail: Optional[str] = None,
) -> ValidationFinding:
    """Build one finding by comparing an expected and an extracted value.

    Comparison is on the rendered text of both sides, after stripping
    surrounding whitespace. Rendering rather than comparing raw objects
    is deliberate: 2 and 2.0 must not be called a mismatch, while "2 V"
    and "2 A" must, and the finding has to be able to show both sides to
    a reviewer.
    """

    if unsupported:
        return ValidationFinding(
            part_id=part_id,
            field=field,
            status=SourceCheckStatus.UNSUPPORTED,
            extracted=_render(extracted),
            detail=detail,
        )
    if unreadable_in_source:
        return ValidationFinding(
            part_id=part_id,
            field=field,
            status=SourceCheckStatus.UNREADABLE_IN_SOURCE,
            expected=_render(expected),
            extracted=_render(extracted),
            detail=detail,
        )
    if expected is None:
        return ValidationFinding(
            part_id=part_id,
            field=field,
            status=SourceCheckStatus.NOT_CHECKED,
            extracted=_render(extracted),
            detail=detail,
        )

    expected_text = _render(expected)
    extracted_text = _render(extracted)
    status = (
        SourceCheckStatus.MATCHES_SOURCE
        if expected_text == extracted_text
        else SourceCheckStatus.MISMATCH
    )
    return ValidationFinding(
        part_id=part_id,
        field=field,
        status=status,
        expected=expected_text,
        extracted=extracted_text,
        detail=detail,
    )


def normalize_number(value: Optional[float]) -> Optional[str]:
    """Render a number canonically so 2, 2.0 and 2.00 compare equal.

    Keeps the sign: -2 and 2 must stay different, because a flipped sign
    is one of the extraction failures this module exists to catch.
    """

    if value is None:
        return None
    if float(value) == int(value):
        return str(int(value))
    return repr(float(value))


def _render(value: Optional[object]) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return normalize_number(float(value))
    return str(value).strip()
