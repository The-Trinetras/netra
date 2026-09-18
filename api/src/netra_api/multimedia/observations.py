"""Invariants that keep an ObservationSource honest about its value.

netra_api.multimedia.evidence.ObservationSource records *how* a detail
was obtained. That classification is only worth carrying if the value
beside it is consistent with it, so this module holds the one rule
every multimedia structure applies to every classified field:

    UNREADABLE means there is no value.

A label that could not be read cannot also be a string; if it could,
the classification would be decoration on top of a guess, which is
exactly what multimedia.md's "Never invent a label, relationship,
coordinate or measurement" forbids.

There is deliberately no matching rule that OBSERVED must carry a
value. OBSERVED with no value is a real and useful statement: somebody
looked and the detail is not there. An axis with no printed unit, a
shape with no caption, a cell that is genuinely blank — all of those
are observations of absence, and they are not the same as UNREADABLE,
which means the detail *is* there and could not be read. Collapsing
them would force extraction to invent a unit for every unitless axis.

GENERATED and ESTIMATED are unconstrained: both are honest about being
non-authoritative, and both may carry a value or omit one.
"""

from __future__ import annotations

from typing import Optional

from netra_api.multimedia.evidence import ObservationSource


class ObservationInvariantError(ValueError):
    """Raised when a classified field's value contradicts its ObservationSource.

    A ValueError subclass so pydantic model validators surface it as an
    ordinary validation error rather than as an unexpected failure.
    """


def check_observation(field: str, source: ObservationSource, value: Optional[object]) -> None:
    """Enforce the UNREADABLE invariant for one classified field."""

    if source is ObservationSource.UNREADABLE and value is not None:
        raise ObservationInvariantError(
            f"{field} is marked unreadable but carries the value {value!r}; "
            "an unreadable detail must not be filled in"
        )


def is_authoritative(source: ObservationSource) -> bool:
    """Whether a detail may be presented to a student as a fact about the source.

    Only a directly observed detail qualifies. An estimated value is
    approximate by definition, a generated one is model prose, and an
    unreadable one is a stated gap. Callers that need to make a factual
    claim, such as "the x axis is current in amperes", gate on this.

    Callers must still check that the value is present: OBSERVED with no
    value is an observation of absence, which is authoritative but is not
    a claim about what the detail says.
    """

    return source is ObservationSource.OBSERVED
