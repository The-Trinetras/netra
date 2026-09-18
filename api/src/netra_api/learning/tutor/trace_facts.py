"""Allowlisted, content-free facts about one Tutor turn, for M1's tracing boundary.

INT-12 (docs/team/integration-playbook.md) and the AX plan
(docs/architecture/arize-ax-integration.md, "Data minimization and
correlation"). M1 owns the tracer, sanitization pipeline, exporter and
lifecycle; domain code must not depend on AX or OpenTelemetry types. M4's
part is deciding WHAT about a Tutor turn may be recorded, so this module
produces a flat mapping of primitive values that M1's boundary can attach
to its Tutor span. It imports no telemetry library and performs no I/O.

Every key is listed in TUTOR_TRACE_ATTRIBUTE_KEYS. Values are built only
from enumerations, counts, booleans and opaque identifiers; no field of
the handoff, result, question or proposal that carries text is read. In
particular these never appear: the student's utterance or answer, recent
dialogue, learning goal, public segment text, decision summary, question
prompt/options, answer key or rubric, evidence text, and concept ids.
The decision summary is excluded even though it is "observable": it is
free text and can quote question details.

Opaque identifiers included: handoff_id and request_id (correlation with
M1's request span and evaluation cases), and the evidence ids actually
used (permitted evidence references under the AX plan). Evidence ids are
references, not content; M1 may still drop them if the reviewed allowlist
says so.
"""

from __future__ import annotations

from typing import Mapping, Union

from netra_api.coordinator.handoff import CoordinatorToTutorHandoff
from netra_api.coordinator.limits import TurnBudget
from netra_api.learning.tutor.runner import TutorTurnOutcome

TraceValue = Union[str, int, bool]

TUTOR_TRACE_ATTRIBUTE_KEYS = frozenset(
    {
        "netra.tutor.handoff_id",
        "netra.tutor.request_id",
        "netra.tutor.mode",
        "netra.tutor.status",
        "netra.tutor.segment_kinds",
        "netra.tutor.segment_count",
        "netra.tutor.evidence_requested_count",
        "netra.tutor.evidence_used_count",
        "netra.tutor.evidence_ids",
        "netra.tutor.committed_event_types",
        "netra.tutor.uncommitted_event_types",
        "netra.tutor.pending_question_change",
        "netra.tutor.had_pending_question",
        "netra.tutor.model_decisions_used",
        "netra.tutor.tool_calls_used",
        "netra.tutor.cancelled",
    }
)


def tutor_trace_attributes(
    handoff: CoordinatorToTutorHandoff, outcome: TutorTurnOutcome, budget: TurnBudget
) -> Mapping[str, TraceValue]:
    """Facts about a completed Tutor turn, restricted to the allowlist.

    ``budget`` is the originating turn's budget, so the counters are the
    turn's cumulative usage (Coordinator plus Tutor), not the Tutor's share.
    """

    result = outcome.result
    facts: dict[str, TraceValue] = {
        "netra.tutor.handoff_id": str(handoff.handoff_id),
        "netra.tutor.request_id": str(handoff.request_id),
        "netra.tutor.mode": handoff.mode,
        "netra.tutor.status": result.status,
        "netra.tutor.segment_kinds": ",".join(segment.kind for segment in result.public_segments),
        "netra.tutor.segment_count": len(result.public_segments),
        "netra.tutor.evidence_requested_count": len(handoff.evidence_refs),
        "netra.tutor.evidence_used_count": len(result.evidence_ids),
        "netra.tutor.evidence_ids": ",".join(result.evidence_ids),
        "netra.tutor.committed_event_types": ",".join(e.event_type for e in outcome.committed_events),
        "netra.tutor.uncommitted_event_types": ",".join(
            e.event_type for e in outcome.uncommitted_events
        ),
        "netra.tutor.pending_question_change": outcome.pending_question.change.value,
        "netra.tutor.had_pending_question": handoff.pending_question is not None,
        "netra.tutor.model_decisions_used": budget.model_decisions_used,
        "netra.tutor.tool_calls_used": budget.tool_calls_used,
        "netra.tutor.cancelled": outcome.cancelled_during_turn,
    }
    return facts
