"""Deterministic navigation: the ten commands, handled without any model.

coordinator.md semantics implemented here:

- stop: cancel output and any in-flight turn; keep the acknowledged position.
  No closing speech, no position change.
- pause: pause eligible unfinished playback; nothing is cancelled.
- continue: resume a paused generation; otherwise read onward from the
  current position. It never reactivates a cancelled or superseded
  generation (the registry cannot resume a cancelled one).
- next/previous: move by the requested navigation unit using M2's position
  resolver; jumps by a unit larger than a sentence record an undo position.
- repeat: follows the interaction mode — the current sentence (or block)
  when reading; the same persisted pending question in a lesson/quiz.
- where_am_i: orientation text only; never moves position or interrupts.
- back_to_reading: restore the recorded return position; the pending
  question survives.
- undo_jump: swap back to the position before the last jump.
- return_to_question: restore the EXISTING pending question from the
  Learning repository; never a newly generated one.

Position only advances through navigation here or through acknowledged
playback (SessionService.apply_playback_ack), never because content was sent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from netra_api.platform.auth_context import AuthContext
from netra_api.platform.awaitables import maybe_await
from netra_api.platform.errors import NetraError, ResourceUnavailableError, StaleRequestError
from netra_api.session.commands import NavigationCommandName
from netra_api.session.dialogue import DialogueLog
from netra_api.session.modes import InteractionMode, NavigationUnit
from netra_api.session.outputs import PlannedQuestion, PlannedSegment, ResponsePlan
from netra_api.session.reading import ReadingAccess
from netra_api.session.state import ReadingPosition, SessionState

NO_SOURCE_NOTICE = "No source is open yet."
NOTHING_TO_UNDO_NOTICE = "There is no jump to undo."
NO_QUESTION_NOTICE = "No question is waiting."
NOTHING_TO_REPEAT_NOTICE = "There is nothing to repeat yet."
NOTHING_PAUSED_NOTICE = "Nothing is paused. Say back to reading or return to question."
END_NOTICE = "You are at the end of this source."
START_NOTICE = "You are at the start of this source."


@dataclass(frozen=True)
class NavigationOutcome:
    new_state: Optional[SessionState]
    """The next state without its version advanced, or None when the command
    changes nothing durable."""
    plan: ResponsePlan


def public_question_plan(approved: Any, hints_used: int) -> PlannedQuestion:
    """Project a persisted question onto its public fields only.

    Built attribute by attribute from the server-side question so nothing
    else (answer_key, rubric, grading notes) can be carried along.
    """

    return PlannedQuestion(
        question_id=str(approved.question_id),
        question_version=int(approved.question_version),
        kind=getattr(approved.kind, "value", approved.kind),
        prompt=approved.prompt,
        options=tuple((str(option.option_id), str(option.text)) for option in approved.options),
        hints_used=hints_used,
    )


class DeterministicNavigator:
    def __init__(
        self,
        reading: Optional[ReadingAccess],
        pending_questions: Any = None,
        dialogue: Optional[DialogueLog] = None,
    ) -> None:
        self._reading = reading
        self._pending_questions = pending_questions
        self._dialogue = dialogue

    async def handle(
        self,
        auth: AuthContext,
        state: SessionState,
        command: NavigationCommandName,
        unit: Optional[NavigationUnit],
        *,
        has_paused_generation: bool,
    ) -> NavigationOutcome:
        unit = unit or NavigationUnit.SENTENCE
        handler = {
            NavigationCommandName.STOP: self._stop,
            NavigationCommandName.PAUSE: self._pause,
            NavigationCommandName.CONTINUE: self._continue,
            NavigationCommandName.NEXT: self._next,
            NavigationCommandName.PREVIOUS: self._previous,
            NavigationCommandName.REPEAT: self._repeat,
            NavigationCommandName.WHERE_AM_I: self._where_am_i,
            NavigationCommandName.BACK_TO_READING: self._back_to_reading,
            NavigationCommandName.UNDO_JUMP: self._undo_jump,
            NavigationCommandName.RETURN_TO_QUESTION: self._return_to_question,
        }[command]
        return await handler(auth, state, unit, has_paused_generation)

    # -- playback control -------------------------------------------------

    async def _stop(self, auth, state, unit, paused) -> NavigationOutcome:
        return NavigationOutcome(None, ResponsePlan(playback="cancel", cancel_turn=True, speak=False))

    async def _pause(self, auth, state, unit, paused) -> NavigationOutcome:
        return NavigationOutcome(None, ResponsePlan(playback="pause", speak=False))

    async def _continue(self, auth, state, unit, paused) -> NavigationOutcome:
        if paused:
            return NavigationOutcome(None, ResponsePlan(playback="resume", speak=False))
        if state.interaction_mode in (InteractionMode.TUTOR_LESSON, InteractionMode.QUIZ):
            return NavigationOutcome(None, ResponsePlan.notice(NOTHING_PAUSED_NOTICE))

        position = await self._current_or_first(auth, state)
        if position is None:
            return NavigationOutcome(None, ResponsePlan.notice(NO_SOURCE_NOTICE))

        acknowledged = state.last_playback_ack.sentence_id if state.last_playback_ack else None
        start = position
        if acknowledged is not None and acknowledged == position.current_sentence_id:
            following = await self._require_reading().step(position, NavigationUnit.SENTENCE, forward=True)
            if following is None:
                return NavigationOutcome(None, ResponsePlan.notice(END_NOTICE))
            start = following

        segments = await self._segments(start, through_block_end=True)
        new_state = self._with(state, interaction_mode=InteractionMode.READING, reading_position=position)
        return NavigationOutcome(new_state, ResponsePlan(playback="cancel", cancel_turn=True, segments=segments))

    # -- movement ---------------------------------------------------------

    async def _next(self, auth, state, unit, paused) -> NavigationOutcome:
        return await self._move(auth, state, unit, forward=True)

    async def _previous(self, auth, state, unit, paused) -> NavigationOutcome:
        return await self._move(auth, state, unit, forward=False)

    async def _move(self, auth, state: SessionState, unit: NavigationUnit, *, forward: bool) -> NavigationOutcome:
        position = await self._current_or_first(auth, state)
        if position is None:
            return NavigationOutcome(None, ResponsePlan.notice(NO_SOURCE_NOTICE))

        destination = await self._require_reading().step(position, unit, forward=forward)
        if destination is None:
            return NavigationOutcome(None, ResponsePlan.notice(END_NOTICE if forward else START_NOTICE))
        self._assert_same_source(position, destination)

        updates: dict[str, Any] = {
            "reading_position": destination,
            "interaction_mode": InteractionMode.READING,
        }
        if unit != NavigationUnit.SENTENCE:
            updates["undo_jump_position"] = position
        segments = await self._segments(destination, through_block_end=unit != NavigationUnit.SENTENCE)
        return NavigationOutcome(
            self._with(state, **updates), ResponsePlan(playback="cancel", cancel_turn=True, segments=segments)
        )

    async def _back_to_reading(self, auth, state: SessionState, unit, paused) -> NavigationOutcome:
        target = state.reading_return_position
        if target is None or not target.has_location:
            target = await self._current_or_first(auth, state)
            if target is None:
                return NavigationOutcome(None, ResponsePlan.notice(NO_SOURCE_NOTICE))
        self._assert_pinned(state, target)
        await self._require_reading().assert_source_access(auth, target.source_version_id)

        updates: dict[str, Any] = {
            "reading_position": target,
            "interaction_mode": InteractionMode.READING,
            "reading_return_position": None,
        }
        if state.reading_position.has_location and state.reading_position != target:
            updates["undo_jump_position"] = state.reading_position
        segments = await self._segments(target, through_block_end=False)
        return NavigationOutcome(
            self._with(state, **updates), ResponsePlan(playback="cancel", cancel_turn=True, segments=segments)
        )

    async def _undo_jump(self, auth, state: SessionState, unit, paused) -> NavigationOutcome:
        target = state.undo_jump_position
        if target is None or not target.has_location:
            return NavigationOutcome(None, ResponsePlan.notice(NOTHING_TO_UNDO_NOTICE))
        self._assert_pinned(state, target)
        await self._require_reading().assert_source_access(auth, target.source_version_id)

        updates: dict[str, Any] = {
            "reading_position": target,
            "undo_jump_position": state.reading_position if state.reading_position.has_location else None,
            "interaction_mode": InteractionMode.READING,
        }
        segments = await self._segments(target, through_block_end=False)
        return NavigationOutcome(
            self._with(state, **updates), ResponsePlan(playback="cancel", cancel_turn=True, segments=segments)
        )

    # -- orientation and repetition ---------------------------------------

    async def _repeat(self, auth, state: SessionState, unit, paused) -> NavigationOutcome:
        if state.interaction_mode in (InteractionMode.TUTOR_LESSON, InteractionMode.QUIZ):
            if state.pending_question is not None:
                question = await self.load_pending_question(auth, state)
                return NavigationOutcome(None, ResponsePlan(playback="cancel", cancel_turn=True, question=question))
            segments = await self._last_delivered_segments(state)
            if not segments:
                return NavigationOutcome(None, ResponsePlan.notice(NOTHING_TO_REPEAT_NOTICE))
            return NavigationOutcome(None, ResponsePlan(playback="cancel", cancel_turn=True, segments=segments))

        if not state.reading_position.has_location:
            return NavigationOutcome(None, ResponsePlan.notice(NO_SOURCE_NOTICE))
        await self._require_reading().assert_source_access(auth, state.reading_position.source_version_id)
        whole_block = unit == NavigationUnit.BLOCK
        segments = await self._segments(state.reading_position, through_block_end=whole_block, whole_block=whole_block)
        return NavigationOutcome(None, ResponsePlan(playback="cancel", cancel_turn=True, segments=segments))

    async def _where_am_i(self, auth, state: SessionState, unit, paused) -> NavigationOutcome:
        parts: list[str] = []
        position = state.reading_position
        if position.has_location:
            reading = self._require_reading()
            await reading.assert_source_access(auth, position.source_version_id)
            block = await reading.block(position)
            total = await reading.block_count(position.source_version_id)
            sentence_index = self._sentence_index(block, position.current_sentence_id)
            where = f"Block {block.ordinal + 1} of {total}"
            if sentence_index is not None:
                where += f", sentence {sentence_index + 1} of {len(block.sentences)}"
            if block.locator:
                where += f", {block.locator}"
            parts.append(where + ".")
        else:
            parts.append(NO_SOURCE_NOTICE)
        if state.active_lesson is not None:
            parts.append("A lesson is in progress.")
        if state.pending_question is not None:
            parts.append("A question is waiting.")
        return NavigationOutcome(None, ResponsePlan.notice(" ".join(parts)))

    async def _return_to_question(self, auth, state: SessionState, unit, paused) -> NavigationOutcome:
        if state.pending_question is None:
            return NavigationOutcome(None, ResponsePlan.notice(NO_QUESTION_NOTICE))
        try:
            question = await self.load_pending_question(auth, state)
        except StaleRequestError:
            # The Learning store no longer holds it pending (answered or
            # withdrawn): drop the stale reference atomically with this
            # request instead of failing every later attempt. Never a new one.
            return NavigationOutcome(state.without_pending_question(), ResponsePlan.notice(NO_QUESTION_NOTICE))

        updates: dict[str, Any] = {
            "interaction_mode": InteractionMode.TUTOR_LESSON if state.active_lesson else InteractionMode.QUIZ
        }
        if state.interaction_mode == InteractionMode.READING and state.reading_position.has_location:
            updates["reading_return_position"] = state.reading_position
        return NavigationOutcome(
            self._with(state, **updates), ResponsePlan(playback="cancel", cancel_turn=True, question=question)
        )

    # -- helpers ------------------------------------------------------------

    def _require_reading(self) -> ReadingAccess:
        if self._reading is None:
            raise ResourceUnavailableError("reading services are not registered")
        return self._reading

    async def _current_or_first(self, auth: AuthContext, state: SessionState) -> Optional[ReadingPosition]:
        position = state.reading_position
        if position.source_version_id is None:
            return None
        reading = self._require_reading()
        await reading.assert_source_access(auth, position.source_version_id)
        if position.has_location:
            return position
        return await reading.first(position.source_version_id)

    @staticmethod
    def _assert_same_source(current: ReadingPosition, destination: ReadingPosition) -> None:
        if destination.source_version_id != current.source_version_id:
            raise ResourceUnavailableError("position resolver returned a different source version")

    @staticmethod
    def _assert_pinned(state: SessionState, target: ReadingPosition) -> None:
        pinned = state.reading_position.source_version_id
        if pinned is not None and target.source_version_id != pinned:
            raise StaleRequestError("recorded position belongs to a different source version")

    @staticmethod
    def _sentence_index(block: Any, sentence_id: Optional[str]) -> Optional[int]:
        if sentence_id is None:
            return None
        for index, sentence in enumerate(block.sentences):
            if str(sentence.sentence_id) == sentence_id:
                return index
        return None

    async def _segments(
        self, position: ReadingPosition, *, through_block_end: bool, whole_block: bool = False
    ) -> tuple[PlannedSegment, ...]:
        block = await self._require_reading().block(position)
        if not block.sentences:
            raise ResourceUnavailableError("the block at this position has no readable sentences")
        if whole_block or position.current_sentence_id is None:
            start = 0
        else:
            start = self._sentence_index(block, position.current_sentence_id)
            if start is None:
                raise ResourceUnavailableError("the recorded sentence is not in its block")
        chosen = block.sentences[start:] if (through_block_end or whole_block) else block.sentences[start : start + 1]
        return tuple(
            PlannedSegment(
                origin="source_reading",
                text=sentence.text,
                source_version_id=position.source_version_id,
                block_id=str(block.block_id),
                sentence_id=str(sentence.sentence_id),
            )
            for sentence in chosen
            if sentence.text
        )

    async def load_pending_question(self, auth: AuthContext, state: SessionState) -> PlannedQuestion:
        pending = state.pending_question
        if self._pending_questions is None:
            raise ResourceUnavailableError("pending question storage is not registered")
        try:
            approved = await maybe_await(self._pending_questions.get_pending(auth, pending.question_id))
        except NetraError:
            raise
        except Exception as exc:  # teammate boundary
            raise ResourceUnavailableError("pending question storage is unavailable") from exc
        if approved is None or int(approved.question_version) != pending.question_version:
            raise StaleRequestError("the pending question is no longer available in that version")
        return public_question_plan(approved, pending.hints_used)

    async def _last_delivered_segments(self, state: SessionState) -> tuple[PlannedSegment, ...]:
        if self._dialogue is None:
            return ()
        entries = await self._dialogue.recent(state.session_id, 16)
        replies = [entry for entry in entries if entry.role != "student"]
        if not replies:
            return ()
        last_request = replies[-1].request_id
        return tuple(
            PlannedSegment(origin="generated", kind="explanation", text=entry.content)
            for entry in replies
            if entry.request_id == last_request
        )

    @staticmethod
    def _with(state: SessionState, **updates: Any) -> SessionState:
        return state.model_copy(update=updates)
