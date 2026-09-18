"""Typed, bounded tool gateway for the Coordinator.

The Coordinator never accesses PostgreSQL, Neo4j, Pinecone or a provider
directly. Every model-requested action passes through ToolGateway.dispatch,
which in order:

1. exposes and accepts only tools registered for the current context
   (interaction mode, pinned source);
2. validates model-supplied arguments against the tool's strict input model
   (unknown keys rejected — model arguments can never carry identity or an
   authority override; trusted identity comes from ToolContext);
3. reserves tool invocations atomically from the SHARED originating budget
   before any call starts, so parallel batches cannot overshoot;
4. caps each call's timeout by the remaining turn time and abandons calls the
   moment the turn is cancelled;
5. validates each result's type, bounds and source-version pinning.

Tool implementations live with the owning service's adapters
(coordinator/tools.py wraps M2/M3 services); nothing here trusts a tool to
have checked its own output.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Literal, Optional, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from netra_api.content.retrieval.evidence import EvidenceTrust
from netra_api.coordinator.limits import TurnBudget
from netra_api.coordinator.providers.gemini import ToolCallRequest
from netra_api.multimedia.evidence import ObservationSource
from netra_api.platform.auth_context import AuthContext
from netra_api.platform.errors import NetraError
from netra_api.platform.observability import TurnTrace
from netra_api.platform.tracing import DISABLED_TRACER, Tracer
from netra_api.session.modes import InteractionMode
from netra_api.session.state import SessionState

logger = logging.getLogger(__name__)

MAX_EVIDENCE_PER_RESULT = 12
MAX_EVIDENCE_TEXT_CHARS = 4000
MAX_OBSERVATIONS_PER_EVIDENCE = 24


class ToolObservation(BaseModel):
    """One structured detail with its provenance (observed/generated/estimated/unreadable)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str = Field(min_length=1, max_length=200)
    value: Optional[str] = Field(default=None, max_length=1000)
    source: ObservationSource


class ToolEvidence(BaseModel):
    """Authorized, resolved evidence a tool returns. Text is untrusted data."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str = Field(min_length=1, max_length=200)
    source_version_id: str = Field(min_length=1)
    evidence_version: Optional[int] = Field(default=None, ge=1)
    """Required by the typed Tutor handoff's EvidenceRef. M2's Evidence model
    does not expose it yet; delegation fails explicitly while it is absent."""
    locator: str = Field(max_length=500)
    text: str = Field(max_length=MAX_EVIDENCE_TEXT_CHARS)
    provenance: str = Field(max_length=200)
    trust: EvidenceTrust
    observations: tuple[ToolObservation, ...] = Field(default=(), max_length=MAX_OBSERVATIONS_PER_EVIDENCE)


class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence: tuple[ToolEvidence, ...] = Field(default=(), max_length=MAX_EVIDENCE_PER_RESULT)
    rejected_count: int = Field(default=0, ge=0)
    """How many candidates failed authorization/version checks. A count only:
    rejection reasons never reach model context."""


@dataclass
class ToolContext:
    """Trusted runtime context injected into every tool call."""

    auth: AuthContext
    budget: TurnBudget
    session: SessionState
    trace: TurnTrace
    companion_source_version_ids: frozenset[str] = frozenset()
    """Additional source versions in scope for this turn (e.g. the lecture
    matching a pinned PDF). No committed session field records such a
    selection yet, so composition passes none; each entry must already be
    authorized by the caller. See docs/team/handoffs/M1.md."""

    @property
    def pinned_source_version_id(self) -> Optional[str]:
        return self.session.reading_position.source_version_id

    def record_nested_model_call(self, operation: str) -> None:
        self.budget.record_nested_model_call()
        self.trace.record("nested_model_call", operation=operation, total=self.budget.nested_model_calls)


class ToolInvoker(Protocol):
    async def __call__(self, context: ToolContext, arguments: BaseModel) -> ToolResult:
        ...


class ToolDefinition(BaseModel):
    """Describes one bounded tool the Coordinator may request."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    description: str = Field(max_length=500)
    input_model: type[BaseModel]
    timeout_seconds: float = Field(gt=0)
    requires_pinned_source: bool = True
    allowed_modes: frozenset[InteractionMode] = frozenset(InteractionMode)

    def input_schema(self) -> dict[str, Any]:
        return self.input_model.model_json_schema()


DispatchStatus = Literal["ok", "rejected", "not_dispatched", "timeout", "failed", "cancelled"]


@dataclass(frozen=True)
class ToolDispatchOutcome:
    request: ToolCallRequest
    status: DispatchStatus
    result: Optional[ToolResult] = None
    reason: str = ""


class ToolRegistry:
    """Maps tool names to their definition and invoker. Registered at startup only."""

    def __init__(self) -> None:
        self._definitions: dict[str, ToolDefinition] = {}
        self._invokers: dict[str, ToolInvoker] = {}

    def register(self, definition: ToolDefinition, invoker: ToolInvoker) -> None:
        if definition.name in self._definitions:
            raise ValueError(f"tool {definition.name!r} is already registered")
        self._definitions[definition.name] = definition
        self._invokers[definition.name] = invoker

    def get_definition(self, name: str) -> ToolDefinition:
        return self._definitions[name]

    def get_invoker(self, name: str) -> ToolInvoker:
        return self._invokers[name]

    def list_tools(self) -> list[ToolDefinition]:
        return list(self._definitions.values())

    def permitted(self, session: SessionState) -> list[ToolDefinition]:
        """The subset exposed for this context; nothing else is described to the model."""

        pinned = session.reading_position.source_version_id is not None
        return [
            definition
            for definition in self._definitions.values()
            if session.interaction_mode in definition.allowed_modes and (pinned or not definition.requires_pinned_source)
        ]


def _argument_key(request: ToolCallRequest) -> str:
    import json

    return request.tool_name + ":" + json.dumps(request.arguments, sort_keys=True, default=str)


class ToolGateway:
    def __init__(self, registry: ToolRegistry, *, tracer: Tracer = DISABLED_TRACER) -> None:
        self._registry = registry
        self._tracer = tracer

    @staticmethod
    def action_key(request: ToolCallRequest) -> str:
        """Stable identity of a requested action, used to detect identical repetition."""

        return _argument_key(request)

    async def dispatch(self, context: ToolContext, requests: list[ToolCallRequest]) -> list[ToolDispatchOutcome]:
        outcomes: dict[int, ToolDispatchOutcome] = {}
        runnable: list[tuple[int, ToolDefinition, BaseModel]] = []
        permitted = {definition.name for definition in self._registry.permitted(context.session)}

        for index, request in enumerate(requests):
            if request.tool_name not in permitted:
                outcomes[index] = ToolDispatchOutcome(request, "rejected", reason="tool_not_permitted")
                context.trace.record("tool_rejected", tool=request.tool_name, reason="tool_not_permitted")
                continue
            definition = self._registry.get_definition(request.tool_name)
            try:
                arguments = definition.input_model.model_validate(request.arguments)
            except ValidationError:
                outcomes[index] = ToolDispatchOutcome(request, "rejected", reason="invalid_arguments")
                context.trace.record("tool_rejected", tool=request.tool_name, reason="invalid_arguments")
                continue
            runnable.append((index, definition, arguments))

        granted = context.budget.reserve_tool_calls(len(runnable))
        for index, definition, _ in runnable[granted:]:
            outcomes[index] = ToolDispatchOutcome(requests[index], "not_dispatched", reason="tool_budget_exhausted")
            context.trace.record("tool_rejected", tool=definition.name, reason="tool_budget_exhausted")
        runnable = runnable[:granted]

        async def run_one(index: int, definition: ToolDefinition, arguments: BaseModel) -> None:
            request = requests[index]
            with self._tracer.span("netra.tool", netra_operation="tool_call", netra_tool=definition.name) as span:
                outcome = await self._invoke(context, request, definition, arguments)
                outcomes[index] = outcome
                span.set(
                    netra_tool_status=outcome.status,
                    netra_tool_reason=outcome.reason or None,
                    netra_evidence_count=len(outcome.result.evidence) if outcome.result else 0,
                    netra_rejected_count=outcome.result.rejected_count if outcome.result else 0,
                )
                if outcome.status in ("timeout", "cancelled"):
                    span.fail(outcome.status)
                elif outcome.status == "failed":
                    span.fail("error", outcome.reason)

        if runnable:
            tasks = [asyncio.ensure_future(run_one(index, definition, arguments)) for index, definition, arguments in runnable]
            cancel_waiter = asyncio.ensure_future(context.budget.wait_cancelled())
            try:
                await asyncio.wait([*tasks, cancel_waiter], return_when=asyncio.FIRST_COMPLETED)
                while not cancel_waiter.done() and not all(task.done() for task in tasks):
                    await asyncio.wait([*tasks, cancel_waiter], return_when=asyncio.FIRST_COMPLETED)
            finally:
                cancel_waiter.cancel()
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            for index, definition, _ in runnable:
                outcomes.setdefault(index, ToolDispatchOutcome(requests[index], "cancelled", reason="turn_cancelled"))

        return [outcomes[index] for index in range(len(requests))]

    async def _invoke(
        self, context: ToolContext, request: ToolCallRequest, definition: ToolDefinition, arguments: BaseModel
    ) -> ToolDispatchOutcome:
        timeout = min(definition.timeout_seconds, context.budget.remaining_seconds())
        context.trace.record(
            "tool_dispatched", tool=definition.name, argument_keys=sorted(request.arguments), timeout_s=round(timeout, 3)
        )
        if timeout <= 0:
            return ToolDispatchOutcome(request, "timeout", reason="deadline_reached")
        invoker = self._registry.get_invoker(definition.name)
        try:
            raw = await asyncio.wait_for(invoker(context, arguments), timeout=timeout)
            result = ToolResult.model_validate(raw.model_dump() if isinstance(raw, BaseModel) else raw)
            self._assert_pinned(context, result)
            return ToolDispatchOutcome(request, "ok", result=result)
        except asyncio.TimeoutError:
            return ToolDispatchOutcome(request, "timeout", reason="tool_timeout")
        except asyncio.CancelledError:
            raise
        except (ValidationError, _PinViolation):
            return ToolDispatchOutcome(request, "failed", reason="invalid_tool_result")
        except NetraError as exc:
            return ToolDispatchOutcome(request, "failed", reason=type(exc).__name__)
        except Exception as exc:  # tool boundary: record kind only
            logger.warning("tool %s failed: %s", definition.name, type(exc).__name__)
            return ToolDispatchOutcome(request, "failed", reason="tool_error")

    @staticmethod
    def _assert_pinned(context: ToolContext, result: ToolResult) -> None:
        pinned = context.pinned_source_version_id
        if pinned is None:
            return
        for evidence in result.evidence:
            if evidence.source_version_id != pinned and not _is_companion_source(context, evidence.source_version_id):
                raise _PinViolation()


class _PinViolation(Exception):
    pass


def _is_companion_source(context: ToolContext, source_version_id: str) -> bool:
    return source_version_id in context.companion_source_version_ids
