"""WebSocket message dispatch: authenticated, strict, replay-safe and fenced.

One Connection serves one accepted socket whose credential was verified
before accept. For EVERY inbound message:

1. Strict parse against shared/contracts/protocol/v1 (serializer.py); unknown
   fields, wrong types, interim transcripts and unsupported versions fail.
2. IdentityService re-resolves the principal against the message's session_id
   (expiry, account, device, binding) — the id alone grants nothing.
3. Type-specific handling:
   - navigation.command: deterministic, never a model call.
   - turn.submit: replay -> in-flight identity -> expected version ->
     deterministic command match -> bounded Coordinator turn in the background.
   - response.cancel: cancels the named turn/generation; no output.
   - playback.ack: validated against delivered, non-cancelled content; only
     completed source reading moves the position.
   - session.resume: canonical snapshot, then the SAME persisted pending question.

Control messages are processed in order and quickly; long work (Coordinator
turns, audio streaming) runs in background tasks so STOP is handled while
output is still being produced. Every text or audio send re-checks generation
eligibility, and disconnect fences this connection's output exactly like STOP.
"""

from __future__ import annotations

import asyncio
import json
import logging
import traceback
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional
from uuid import UUID, uuid4

from pydantic import ValidationError

from netra_api.coordinator.budget_ledger import BudgetLedger, BudgetUsage
from netra_api.coordinator.graph import CoordinatorEngine
from netra_api.coordinator.limits import TurnBudget
from netra_api.coordinator.state import CoordinatorTurnState, TurnOutcome
from netra_api.identity.service import IdentityService
from netra_api.platform.auth_context import AuthContext, AuthenticatedPrincipal
from netra_api.platform.errors import (
    IdempotencyConflictError,
    InvalidRequestError,
    NetraError,
    ProviderUnavailableError,
    SessionVersionConflictError,
    StaleRequestError,
    UnsupportedProtocolVersionError,
)
from netra_api.platform.idempotency import check_expected_version, fingerprint_message
from netra_api.platform.observability import TraceSink, TurnTrace
from netra_api.platform.tracing import DISABLED_TRACER, Tracer
from netra_api.session.commands import NavigationCommandName, match_deterministic_command
from netra_api.session.dialogue import DialogueEntry, DialogueLog
from netra_api.session.modes import InteractionMode, NavigationUnit
from netra_api.session.navigation import DeterministicNavigator
from netra_api.session.outputs import ResponsePlan
from netra_api.session.service import SessionService
from netra_api.session.state import ActiveLessonRef, SessionState
from netra_api.speech.playback_metadata import CancelReasonName, DeliveredSentence, Generation, GenerationRegistry
from netra_api.speech.synthesis import SpeechOutput
from netra_api.transport.websocket.serializer import (
    PROTOCOL_VERSION,
    NavigationCommandPayload,
    PlaybackAckPayload,
    ResponseCancelPayload,
    SessionResumePayload,
    TurnSubmitPayload,
    error_payload_for,
    parse_client_message,
    validate_server_payload,
)
from netra_api.transport.websocket.snapshots import build_session_snapshot

logger = logging.getLogger(__name__)


def _log_unexpected(event: str, exc: BaseException) -> None:
    """Log an unexpected error by exception type and stack frames only.

    Messages and notes can carry student text, provider output or private
    answer data (a pydantic ValidationError prints its input values), and
    private answers stay out of ordinary logs.
    """

    frames = "".join(traceback.format_tb(exc.__traceback__)).rstrip()
    logger.error("%s: %s\n%s", event, type(exc).__name__, frames)


SendText = Callable[[str], Awaitable[None]]
SendBytes = Callable[[bytes], Awaitable[None]]

MAX_RETAINED_TURNS = 256
"""Bound on remembered turn identities (for retransmission after disconnect)."""


def _uuid_or_none(value: Any) -> Optional[UUID]:
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Rendering: plan -> recorded response (the replayable result of a request)
# ---------------------------------------------------------------------------


def render_response(state: SessionState, plan: ResponsePlan, generation_id: Optional[str]) -> dict[str, Any]:
    """Render the messages a request produces, in delivery order.

    The rendered dict is exactly what is committed as the request's replay
    record, so an identical retransmission re-sends the same text payloads.
    Audio is never part of a replay.
    """

    messages: list[dict[str, Any]] = [
        {"type": "session.snapshot", "payload": build_session_snapshot(state).model_dump(mode="json")}
    ]
    sentences: list[dict[str, Any]] = []
    for index, segment in enumerate(plan.segments):
        if segment.origin == "source_reading":
            segment_id = segment.block_id or f"seg-{index}"
            sentence_id = segment.sentence_id or str(uuid4())
        else:
            segment_id = f"seg-{index}"
            sentence_id = str(uuid4())
        payload: dict[str, Any] = {
            "generation_id": generation_id,
            "segment_id": segment_id,
            "sentence_id": sentence_id,
            "text": segment.text,
            "final": index == len(plan.segments) - 1,
        }
        if segment.origin == "generated" and segment.kind is not None:
            payload["kind"] = segment.kind
        if segment.evidence_ids:
            payload["evidence_ids"] = list(dict.fromkeys(segment.evidence_ids))
        validate_server_payload("response.segment", payload)
        messages.append({"type": "response.segment", "payload": payload})
        sentences.append(
            {
                "segment_id": segment_id,
                "sentence_id": sentence_id,
                "origin": segment.origin,
                "source_version_id": segment.source_version_id,
                "block_id": segment.block_id,
            }
        )
    if plan.question is not None:
        question = plan.question
        payload = {
            "question_id": question.question_id,
            "question_version": question.question_version,
            "kind": question.kind,
            "prompt": question.prompt,
            "options": [{"option_id": option_id, "text": text} for option_id, text in question.options],
            "hints_used": question.hints_used,
        }
        validate_server_payload("quiz.question", payload)
        messages.append({"type": "quiz.question", "payload": payload})
    return {
        "generation_id": generation_id,
        "speak": plan.speak,
        "messages": messages,
        "sentences": sentences,
    }


# ---------------------------------------------------------------------------
# In-flight turn identity
# ---------------------------------------------------------------------------


@dataclass
class TurnEntry:
    account_id: UUID
    session_id: UUID
    request_id: UUID
    fingerprint: str
    budget: TurnBudget
    connection_id: UUID
    task: Optional[asyncio.Task] = None
    cancel_reason: Optional[CancelReasonName] = None

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()


class TurnRegistry:
    """Process-wide in-flight turns and their budgets.

    A retransmission of a turn that was cancelled by DISCONNECT (and so never
    recorded a result) re-runs on the SAME TurnBudget: spent model decisions,
    tool calls and the original deadline carry over. A fresh budget only
    exists for a genuinely new request_id.
    """

    def __init__(self, max_retained: int = MAX_RETAINED_TURNS) -> None:
        self._entries: OrderedDict[tuple[UUID, UUID], TurnEntry] = OrderedDict()
        self._max = max_retained

    def get(self, account_id: UUID, request_id: UUID) -> Optional[TurnEntry]:
        return self._entries.get((account_id, request_id))

    def put(self, entry: TurnEntry) -> None:
        self._entries[(entry.account_id, entry.request_id)] = entry
        self._entries.move_to_end((entry.account_id, entry.request_id))
        while len(self._entries) > self._max:
            key, oldest = next(iter(self._entries.items()))
            if oldest.running:
                break
            self._entries.pop(key)

    def cancel_session(self, account_id: UUID, session_id: UUID, reason: CancelReasonName, except_request: Optional[UUID] = None) -> None:
        for entry in self._entries.values():
            if entry.account_id == account_id and entry.session_id == session_id and entry.running and entry.request_id != except_request:
                entry.cancel_reason = reason
                entry.budget.cancel()

    def cancel_request(self, account_id: UUID, session_id: UUID, request_id: UUID, reason: CancelReasonName) -> bool:
        entry = self._entries.get((account_id, request_id))
        if entry is None or entry.session_id != session_id or not entry.running:
            return False
        entry.cancel_reason = reason
        entry.budget.cancel()
        return True

    def cancel_connection(self, connection_id: UUID) -> None:
        for entry in self._entries.values():
            if entry.connection_id == connection_id and entry.running:
                entry.cancel_reason = "disconnect"
                entry.budget.cancel()

    def forget(self, entry: TurnEntry) -> None:
        self._entries.pop((entry.account_id, entry.request_id), None)


# ---------------------------------------------------------------------------
# Services and connection
# ---------------------------------------------------------------------------


@dataclass
class TransportServices:
    identity: IdentityService
    sessions: SessionService
    navigator: DeterministicNavigator
    generations: GenerationRegistry
    turns: TurnRegistry
    trace_sink: TraceSink
    coordinator: Optional[CoordinatorEngine] = None
    dialogue: Optional[DialogueLog] = None
    speech: Optional[SpeechOutput] = None
    tracer: Tracer = DISABLED_TRACER
    budgets: Optional[BudgetLedger] = None
    """D-BUDGET: persisted use per request id. None only in fixture journeys."""


@dataclass
class Connection:
    services: TransportServices
    principal: AuthenticatedPrincipal
    send_text_raw: SendText
    send_bytes_raw: SendBytes
    connection_id: UUID = field(default_factory=uuid4)
    _sequence: int = 0
    _send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _sessions: set[UUID] = field(default_factory=set)
    _tasks: set[asyncio.Task] = field(default_factory=set)
    _budget_writes: set[asyncio.Task] = field(default_factory=set)
    _closed: bool = False

    # -- sending ---------------------------------------------------------------

    async def _send_message(self, session_id: UUID, request_id: UUID, message_type: str, payload: dict) -> None:
        if self._closed:
            return
        async with self._send_lock:
            envelope = {
                "protocol_version": PROTOCOL_VERSION,
                "message_id": str(uuid4()),
                "session_id": str(session_id),
                "request_id": str(request_id),
                "sequence": self._sequence,
                "type": message_type,
                "payload": payload,
            }
            self._sequence += 1
            await self.send_text_raw(json.dumps(envelope, separators=(",", ":")))

    async def _send_bytes(self, frame: bytes) -> None:
        if self._closed:
            return
        async with self._send_lock:
            await self.send_bytes_raw(frame)

    async def _send_error(self, session_id: UUID, request_id: UUID, exc: NetraError, current_version: Optional[int] = None) -> None:
        payload = error_payload_for(exc, current_session_version=current_version)
        if isinstance(exc, InvalidRequestError) and exc.field:
            payload = payload.model_copy(update={"details": {"field": exc.field}})
        await self._send_message(session_id, request_id, "error", payload.model_dump(mode="json", exclude_none=True))

    def _spawn(self, coroutine: Awaitable[Any]) -> asyncio.Task:
        task = asyncio.ensure_future(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    # -- inbound -----------------------------------------------------------------

    async def handle_text(self, raw_text: str) -> None:
        try:
            raw = json.loads(raw_text)
        except json.JSONDecodeError:
            raise InvalidRequestError("frame is not JSON")
        if not isinstance(raw, dict):
            raise InvalidRequestError("frame is not an object")

        session_id = _uuid_or_none(raw.get("session_id"))
        request_id = _uuid_or_none(raw.get("request_id"))
        if session_id is None or request_id is None:
            raise InvalidRequestError("frame lacks valid correlation identifiers")

        try:
            envelope, payload = parse_client_message(raw)
        except UnsupportedProtocolVersionError as exc:
            await self._send_error(session_id, request_id, exc)
            return
        except ValidationError as exc:
            field_name = ".".join(str(part) for part in exc.errors()[0].get("loc", ())[:2]) if exc.errors() else None
            await self._send_error(session_id, request_id, InvalidRequestError("invalid message", field=field_name))
            return

        with self.services.tracer.span(
            "netra.request",
            netra_operation="client_message",
            netra_message_type=envelope.type,
            netra_request_id=str(envelope.request_id),
        ) as span:
            code = await self._dispatch(envelope, payload)
            span.set(netra_outcome="error" if code else "handled", netra_error_code=code)
            if code:
                span.fail("error", code)

    async def _dispatch(self, envelope: Any, payload: Any) -> Optional[str]:
        """Handle one validated message; return a safe error code when one was sent."""

        try:
            auth = await self.services.identity.resolve_auth_context(self.principal, envelope.session_id, envelope.request_id)
            self._sessions.add(envelope.session_id)
            if isinstance(payload, NavigationCommandPayload):
                await self._navigation(auth, payload, fingerprint_message(envelope.type, payload), payload.expected_session_version, payload.command, payload.navigation_unit)
            elif isinstance(payload, TurnSubmitPayload):
                await self._turn_submit(auth, payload)
            elif isinstance(payload, ResponseCancelPayload):
                await self._cancel(auth, payload)
            elif isinstance(payload, PlaybackAckPayload):
                await self._playback_ack(auth, payload)
            elif isinstance(payload, SessionResumePayload):
                await self._resume(auth)
            return None
        except SessionVersionConflictError as exc:
            await self._send_error(envelope.session_id, envelope.request_id, exc, current_version=exc.actual_version)
            return "session_version_conflict"
        except NetraError as exc:
            await self._send_error(envelope.session_id, envelope.request_id, exc)
            from netra_api.platform.errors import error_code_for

            return error_code_for(exc).lower()
        except Exception as exc:
            _log_unexpected(f"unhandled error while dispatching {envelope.type}", exc)
            await self._send_error(envelope.session_id, envelope.request_id, NetraError("internal"))
            return "internal_error"

    async def on_disconnect(self) -> None:
        """Fence everything this connection was producing, exactly like STOP."""

        self._closed = True
        self.services.turns.cancel_connection(self.connection_id)
        for session_id in self._sessions:
            self.services.generations.cancel_speaking(session_id, "disconnect")
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._budget_writes:  # never cancelled: spent budget must be recorded
            await asyncio.gather(*self._budget_writes, return_exceptions=True)

    # -- navigation --------------------------------------------------------------

    async def _navigation(
        self,
        auth: AuthContext,
        payload: Any,
        fingerprint: str,
        expected_version: int,
        command: NavigationCommandName,
        unit: Optional[NavigationUnit],
    ) -> None:
        services = self.services
        paused = services.generations.paused(auth.session_id) is not None
        planned: dict[str, Any] = {}

        async def decide(state: SessionState):
            outcome = await services.navigator.handle(auth, state, command, unit, has_paused_generation=paused)
            generation_id = str(uuid4()) if outcome.plan.segments else None
            planned["plan"] = outcome.plan

            def render(final: SessionState) -> dict[str, Any]:
                return render_response(final, outcome.plan, generation_id)

            return outcome.new_state, render

        result = await services.sessions.handle_request(
            auth,
            request_id=auth.request_id,
            payload_fingerprint=fingerprint,
            expected_version=expected_version,
            decide=decide,
        )
        services.tracer.current().set(
            netra_command=command.value,
            netra_replayed=result.replayed,
            netra_changed=result.changed,
            netra_session_version=result.state.session_version,
        )
        if result.replayed:
            await self._replay(auth, result.result)
            return

        plan: ResponsePlan = planned["plan"]
        cancel_reason: CancelReasonName = "user_stop" if command == NavigationCommandName.STOP else "navigation"
        if plan.cancel_turn:
            services.turns.cancel_session(auth.account_id, auth.session_id, cancel_reason)
        if plan.playback == "cancel":
            services.generations.cancel_speaking(auth.session_id, cancel_reason)
        elif plan.playback == "pause":
            services.generations.pause_active(auth.session_id)
        elif plan.playback == "resume":
            services.generations.resume_paused(auth.session_id)
        await self._deliver(auth, result.result)

    # -- turns -------------------------------------------------------------------

    async def _turn_submit(self, auth: AuthContext, payload: TurnSubmitPayload) -> None:
        services = self.services
        fingerprint = fingerprint_message("turn.submit", payload)

        prior = await services.sessions.recorded_result(auth, auth.request_id, fingerprint)
        if prior is not None:
            await self._replay(auth, prior)
            return

        entry = services.turns.get(auth.account_id, auth.request_id)
        if entry is not None:
            if entry.fingerprint != fingerprint or entry.session_id != auth.session_id:
                raise IdempotencyConflictError(str(auth.request_id))
            if entry.running:
                return  # duplicate delivery of an action already executing; one effect only

        state = await services.sessions.get_state(auth)
        check_expected_version(payload.expected_session_version, state.session_version)

        command = match_deterministic_command(payload.utterance)
        if command is not None:
            await self._navigation(auth, payload, fingerprint, payload.expected_session_version, command, None)
            return

        if services.coordinator is None:
            raise ProviderUnavailableError("the Coordinator is not available")

        if entry is not None:
            budget = entry.budget.for_retransmission()
        elif services.budgets is not None:
            # D-BUDGET: after a restart the same request_id resumes its recorded use and deadline.
            usage = await services.budgets.open(auth.account_id, auth.request_id, auth.session_id, datetime.now(timezone.utc))
            budget = usage.budget()
        else:
            budget = TurnBudget()
        if services.budgets is not None:
            budget.observer = self._budget_observer(services.budgets, auth)
        services.turns.cancel_session(auth.account_id, auth.session_id, "new_turn", except_request=auth.request_id)
        services.generations.cancel_speaking(auth.session_id, "new_turn")
        entry = TurnEntry(
            account_id=auth.account_id,
            session_id=auth.session_id,
            request_id=auth.request_id,
            fingerprint=fingerprint,
            budget=budget,
            connection_id=self.connection_id,
        )
        services.turns.put(entry)
        entry.task = self._spawn(self._run_turn(auth, payload, state, entry))

    def _budget_observer(self, ledger: BudgetLedger, auth: AuthContext) -> Callable[[TurnBudget], None]:
        def observe(budget: TurnBudget) -> None:
            task = asyncio.ensure_future(self._record_budget(ledger, auth, BudgetUsage.of(budget)))
            self._budget_writes.add(task)
            task.add_done_callback(self._budget_writes.discard)

        return observe

    @staticmethod
    async def _record_budget(ledger: BudgetLedger, auth: AuthContext, usage: BudgetUsage) -> None:
        try:
            await ledger.record(auth.account_id, auth.request_id, usage)
        except Exception as exc:  # the turn goes on; the start and earlier use are already recorded
            _log_unexpected("turn budget use not recorded", exc)

    async def _run_turn(self, auth: AuthContext, payload: TurnSubmitPayload, state: SessionState, entry: TurnEntry) -> None:
        services = self.services
        trace = TurnTrace(services.trace_sink, request_id=auth.request_id, session_id=auth.session_id)
        turn = CoordinatorTurnState(
            session_id=auth.session_id,
            request_id=auth.request_id,
            auth=auth,
            budget=entry.budget,
            original_utterance=payload.utterance,
            session=state,
        )
        try:
            outcome = await services.coordinator.run(turn, trace)
            if outcome.kind == "cancelled" and entry.cancel_reason == "disconnect":
                return  # nothing recorded: a retransmission re-runs on this same budget
            await self._commit_and_deliver_turn(auth, payload, entry, outcome)
            services.turns.forget(entry)
        except asyncio.CancelledError:
            raise
        except SessionVersionConflictError as exc:
            await self._send_error(auth.session_id, auth.request_id, exc, current_version=exc.actual_version)
        except NetraError as exc:
            await self._send_error(auth.session_id, auth.request_id, exc)
        except Exception as exc:
            _log_unexpected("unhandled error in Coordinator turn", exc)
            await self._send_error(auth.session_id, auth.request_id, NetraError("internal"))

    async def _commit_and_deliver_turn(
        self, auth: AuthContext, payload: TurnSubmitPayload, entry: TurnEntry, outcome: TurnOutcome
    ) -> None:
        services = self.services
        plan = ResponsePlan(segments=outcome.segments, question=outcome.question)
        generation_id = str(uuid4()) if plan.segments else None
        fingerprint = entry.fingerprint

        async def merge(latest: SessionState) -> Optional[SessionState]:
            updates: dict[str, Any] = {}
            if outcome.lesson_id is not None:
                if latest.active_lesson is None or latest.active_lesson.lesson_id != outcome.lesson_id:
                    updates["active_lesson"] = ActiveLessonRef(lesson_id=outcome.lesson_id)
                updates["interaction_mode"] = InteractionMode.TUTOR_LESSON
                if (
                    latest.interaction_mode == InteractionMode.READING
                    and latest.reading_position.has_location
                    and latest.reading_return_position is None
                ):
                    updates["reading_return_position"] = latest.reading_position
            if outcome.pending_question is not None:
                updates["pending_question"] = outcome.pending_question
            elif outcome.clear_pending_question:
                updates["pending_question"] = None
            return latest.model_copy(update=updates) if updates else None

        def render(final: SessionState) -> dict[str, Any]:
            if outcome.kind == "cancelled":
                return render_response(final, ResponsePlan(), None)
            return render_response(final, plan, generation_id)

        result = await services.sessions.commit_turn(
            auth, request_id=auth.request_id, payload_fingerprint=fingerprint, merge=merge, render=render
        )
        if result.replayed:
            await self._replay(auth, result.result)
            return

        if services.dialogue is not None and outcome.kind != "cancelled":
            now = result.state.updated_at
            entries = [DialogueEntry(session_id=auth.session_id, request_id=auth.request_id, ordinal=0, role="student", content=payload.utterance, created_at=now)]
            entries += [
                DialogueEntry(session_id=auth.session_id, request_id=auth.request_id, ordinal=index + 1, role=outcome.reply_role, content=segment.text, created_at=now)
                for index, segment in enumerate(outcome.segments)
            ]
            await services.dialogue.append(entries)

        if entry.budget.cancelled and outcome.kind != "cancelled":
            # Cancelled after the result was committed: record stays replayable as
            # text, but nothing is delivered or spoken on this connection.
            await self._send_snapshot_only(auth, result.result)
            return
        await self._deliver(auth, result.result)

    # -- cancel / ack / resume ---------------------------------------------------

    async def _cancel(self, auth: AuthContext, payload: ResponseCancelPayload) -> None:
        reason: CancelReasonName = payload.reason or "user_stop"
        self.services.turns.cancel_request(auth.account_id, auth.session_id, payload.cancel_request_id, reason)
        self.services.generations.cancel_for_request(auth.session_id, payload.cancel_request_id, reason)
        if payload.generation_id:
            self.services.generations.cancel_generation(auth.session_id, payload.generation_id, reason)

    async def _playback_ack(self, auth: AuthContext, payload: PlaybackAckPayload) -> None:
        found = self.services.generations.lookup_delivered(
            auth.session_id, payload.generation_id, payload.segment_id, payload.sentence_id
        )
        if found is None:
            raise StaleRequestError("acknowledged content is not eligible")
        generation, sentence = found
        key = (payload.segment_id, payload.sentence_id, payload.status)
        if key in generation.acknowledgements:
            return
        generation.acknowledgements.add(key)
        self.services.tracer.current().set(netra_playback_status=payload.status, netra_generation_id=payload.generation_id)
        state, changed = await self.services.sessions.apply_playback_ack(
            auth, sentence, generation_id=payload.generation_id, status=payload.status, played_ms=payload.played_ms
        )
        if changed:
            await self._send_message(
                auth.session_id, auth.request_id, "session.snapshot", build_session_snapshot(state).model_dump(mode="json")
            )

    async def _resume(self, auth: AuthContext) -> None:
        state = await self.services.sessions.get_state(auth)
        question = None
        if state.pending_question is not None:
            try:
                question = await self.services.navigator.load_pending_question(auth, state)
            except StaleRequestError:
                # Answered (e.g. committed just before a crash) or withdrawn:
                # reconcile the reference rather than failing every resume.
                state = await self.services.sessions.clear_stale_pending_question(auth, state.pending_question)
        await self._send_message(
            auth.session_id, auth.request_id, "session.snapshot", build_session_snapshot(state).model_dump(mode="json")
        )
        if question is not None:
            rendered = render_response(state, ResponsePlan(question=question), None)
            await self._send_message(auth.session_id, auth.request_id, "quiz.question", rendered["messages"][-1]["payload"])

    # -- delivery ------------------------------------------------------------------

    async def _replay(self, auth: AuthContext, result: dict[str, Any]) -> None:
        """Re-send a committed result's text messages. Never audio, never playback control."""

        for message in result.get("messages", []):
            await self._send_message(auth.session_id, auth.request_id, message["type"], message["payload"])

    async def _send_snapshot_only(self, auth: AuthContext, result: dict[str, Any]) -> None:
        for message in result.get("messages", []):
            if message["type"] == "session.snapshot":
                await self._send_message(auth.session_id, auth.request_id, message["type"], message["payload"])

    async def _deliver(self, auth: AuthContext, result: dict[str, Any]) -> None:
        services = self.services
        generation_id = result.get("generation_id")
        generation: Optional[Generation] = None
        if generation_id:
            generation = services.generations.start(
                auth.session_id, auth.request_id, speakable=bool(result.get("speak", True)), generation_id=generation_id
            )
            for sentence in result.get("sentences", []):
                services.generations.record_sentence(generation, DeliveredSentence(**sentence))

        for message in result.get("messages", []):
            if message["type"] == "response.segment" and (generation is None or generation.is_cancelled):
                continue
            await self._send_message(auth.session_id, auth.request_id, message["type"], message["payload"])

        if generation is None:
            return
        if services.speech is None or not generation.speakable:
            services.generations.complete(generation)
            return
        self._spawn(self._speak(auth, generation, result))

    async def _speak(self, auth: AuthContext, generation: Generation, result: dict[str, Any]) -> None:
        segments = [message["payload"] for message in result.get("messages", []) if message["type"] == "response.segment"]
        sentences = {(s["segment_id"], s["sentence_id"]): s for s in result.get("sentences", [])}
        for index, segment in enumerate(segments):
            info = sentences.get((segment["segment_id"], segment["sentence_id"]), {})
            scope = (
                f"source:{info.get('source_version_id')}"
                if info.get("origin") == "source_reading"
                else f"account:{auth.account_id}"
            )
            sent = await self.services.speech.speak_segment(
                auth,
                generation,
                segment_id=segment["segment_id"],
                text=segment["text"],
                access_scope=scope,
                end_of_generation=index == len(segments) - 1,
                send_bytes=self._send_bytes,
            )
            if not sent and generation.is_cancelled:
                return
        self.services.generations.complete(generation)
