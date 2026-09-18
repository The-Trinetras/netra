"""Inspectable action/evidence/outcome traces for one originating turn.

CLAUDE.md: "Log actions, evidence and outcomes, never private chain of
thought." A trace entry therefore has a closed kind vocabulary and a small
set of bounded fields. There is deliberately no free-text "reasoning" field:
a model's rationale is not recorded, only what it asked for (tool name and
argument keys), what came back (evidence identifiers, counts, gap codes)
and what the application decided.

The decisive evidence-repair trace the AgentSpec asks for is expressible
with these kinds alone: ``tool_result`` (insufficient) -> ``evidence_gap``
(specific requirement and gap code) -> ``action_changed`` (different tool or
arguments, citing the gap) -> ``evidence_validated``.

Secrets never enter a trace: ``redact_detail`` drops any key that names a
credential or private grading material and truncates values; callers pass
identifiers rather than evidence bodies or student answers.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Literal, Optional, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

TraceKind = Literal[
    "turn_started",
    "deterministic_command",
    "model_decision",
    "model_output_rejected",
    "tool_dispatched",
    "tool_rejected",
    "tool_result",
    "evidence_rejected",
    "evidence_gap",
    "action_changed",
    "repetition_blocked",
    "evidence_validated",
    "nested_model_call",
    "handoff_sent",
    "handoff_result",
    "handoff_rejected",
    "learning_proposal_forwarded",
    "context_compacted",
    "budget_exhausted",
    "turn_cancelled",
    "turn_completed",
    "output_fenced",
]

_SECRET_KEY = re.compile(
    r"(token|secret|password|credential|authorization|api[_-]?key|answer_key|rubric|grading)", re.I
)
_MAX_DETAIL_VALUE_CHARS = 200
_MAX_DETAIL_ITEMS = 20


def redact_detail(detail: dict[str, Any]) -> dict[str, Any]:
    """Bound and scrub a trace detail mapping.

    Keys naming credentials or private grading material are dropped
    entirely rather than masked, so their presence is not even implied.
    Values are stringified and truncated; nested structures are reduced to
    bounded string forms so no unbounded provider object can be logged.
    """

    cleaned: dict[str, Any] = {}
    for key, value in list(detail.items())[:_MAX_DETAIL_ITEMS]:
        if _SECRET_KEY.search(str(key)):
            continue
        if isinstance(value, (bool, int, float)) or value is None:
            cleaned[str(key)] = value
        elif isinstance(value, (list, tuple, set, frozenset)):
            cleaned[str(key)] = [str(item)[:_MAX_DETAIL_VALUE_CHARS] for item in list(value)[:_MAX_DETAIL_ITEMS]]
        else:
            cleaned[str(key)] = str(value)[:_MAX_DETAIL_VALUE_CHARS]
    return cleaned


class TraceEvent(BaseModel):
    """One observable action or outcome within a turn."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: TraceKind
    request_id: UUID
    session_id: UUID
    at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    detail: dict[str, Any] = Field(default_factory=dict)


class TraceSink(Protocol):
    def emit(self, event: TraceEvent) -> None:
        ...


class InMemoryTraceSink:
    """Collects events for tests and evaluation runs. Not durable storage."""

    def __init__(self) -> None:
        self.events: list[TraceEvent] = []

    def emit(self, event: TraceEvent) -> None:
        self.events.append(event)

    def kinds(self) -> list[str]:
        return [event.kind for event in self.events]

    def of_kind(self, kind: str) -> list[TraceEvent]:
        return [event for event in self.events if event.kind == kind]


class LoggingTraceSink:
    """Writes trace events to the standard logger as structured JSON records."""

    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        self._logger = logger or logging.getLogger("netra.trace")

    def emit(self, event: TraceEvent) -> None:
        self._logger.info("trace %s", event.model_dump_json())


class TurnTrace:
    """Binds a sink to one request so call sites only supply kind and detail."""

    def __init__(self, sink: TraceSink, *, request_id: UUID, session_id: UUID) -> None:
        self._sink = sink
        self.request_id = request_id
        self.session_id = session_id

    def record(self, kind: TraceKind, **detail: Any) -> None:
        self._sink.emit(
            TraceEvent(
                kind=kind,
                request_id=self.request_id,
                session_id=self.session_id,
                detail=redact_detail(detail),
            )
        )


_EVENT_ATTRIBUTE_MAP = {
    "tool": "netra.tool",
    "reason": "netra.tool.reason",
    "status": "netra.outcome",
    "outcome": "netra.outcome",
    "limit": "netra.outcome",
    "evidence_ids": "netra.evidence_ids",
    "evidence_id": "netra.evidence_ids",
    "new_evidence": "netra.evidence_count",
    "count": "netra.rejected_count",
    "requirement": "netra.requirement",
    "reason_requirements": "netra.requirements",
    "gap": "netra.gap",
    "new_tools": "netra.tools",
    "check": "netra.check",
    "mode": "netra.handoff_mode",
    "attempt": "netra.attempt",
    "handoff_id": "netra.handoff_id",
    "already_committed_attempts": "netra.evidence_count",
}


class TracingTraceSink:
    """Re-emits TurnTrace action/evidence/outcome events as events on the current span.

    Detail keys are mapped onto allowlisted attributes and then sanitized by
    the tracer; anything unmapped or free-text is dropped before export. The
    existing structured events therefore reach AX without widening what
    can be exported.
    """

    def __init__(self, tracer) -> None:
        self._tracer = tracer

    def emit(self, event: TraceEvent) -> None:
        span = self._tracer.current()
        attributes = {}
        for key, value in event.detail.items():
            mapped = _EVENT_ATTRIBUTE_MAP.get(key)
            if mapped is None:
                continue
            if mapped == "netra.evidence_ids" and isinstance(value, str):
                value = [value]
            attributes[mapped] = value
        attributes["netra.request_id"] = str(event.request_id)
        span.event(event.kind, **attributes)


class FanOutTraceSink:
    def __init__(self, *sinks: TraceSink) -> None:
        self._sinks = sinks

    def emit(self, event: TraceEvent) -> None:
        for sink in self._sinks:
            try:
                sink.emit(event)
            except Exception:  # a telemetry sink can never break a turn
                logging.getLogger("netra.trace").warning("trace sink failed: %s", type(sink).__name__)
