"""Tutor guardrails.

Encodes the "The Tutor must not" rules from CLAUDE.md. As with
netra_api.coordinator.policies, only mechanically checkable rules are
runtime guards here; the rest are structural — the Tutor is simply
never given a capability that could do them — and are listed below for
reference rather than re-implemented as heuristics.
"""

from __future__ import annotations

from netra_api.coordinator.state import CoordinatorTurnState
from netra_api.platform.errors import NetraError


class CoordinatorStateLeakError(NetraError):
    """Raised if a CoordinatorTurnState ever reaches Tutor code.

    The Tutor may only ever see a
    netra_api.coordinator.handoff.CoordinatorToTutorHandoff (see
    netra_api.learning.tutor.state.TutorTurnState); this guard exists so
    an accidental parameter of the wrong type fails loudly instead of
    silently leaking Coordinator-internal state or chain-of-thought
    (CLAUDE.md "Tutor": "must not ... receive the Coordinator's entire
    conversation history").
    """


def assert_not_coordinator_state(value: object) -> None:
    if isinstance(value, CoordinatorTurnState):
        raise CoordinatorStateLeakError(
            "Tutor code must never receive CoordinatorTurnState; only CoordinatorToTutorHandoff"
        )


STRUCTURALLY_ENFORCED_RESTRICTIONS = (
    "no changing account identity",
    "no granting source access",
    "no directly setting mastery",
    "no directly writing Neo4j",
    "no arbitrary SQL execution",
)
"""Enforced by omission: the Tutor is never given a tool that could do
these things, so no runtime check exists here for them (mirrors
netra_api.coordinator.policies.STRUCTURALLY_ENFORCED_RESTRICTIONS).
Directly setting mastery in particular is structurally impossible
because netra_api.learning.assessment.models.LearningEventProposal has
no status/mastery field — see
netra_api.learning.assessment.service.LearningService.propose_event."""
