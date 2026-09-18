"""Scoped context selection, compaction and prompt rendering for one Coordinator decision.

current-scope.md: each agent receives the current goal, relevant evidence and
selected history; older dialogue may be compacted, but exact positions, pending
questions, source versions and assistance records stay outside summaries.

So the prompt has three clearly separated parts:

- CANONICAL FACTS rendered verbatim from SessionState on every decision. They
  are never summarized, truncated or taken from dialogue.
- Selected recent dialogue (newest entries within a bound), and a short,
  deterministic, explicitly non-authoritative summary of older entries.
  Compaction makes no model call and spends no budget.
- Validated evidence and the requirement/gap ledger, with evidence text and
  dialogue wrapped as untrusted data.

The character bounds are implementation limits for prompt size, not product
policy; they are named constants so evaluation runs can report them.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from importlib import resources
from typing import Optional

from netra_api.coordinator.evidence_check import EvidenceLedger
from netra_api.coordinator.tool_registry import ToolDefinition
from netra_api.platform.observability import TurnTrace
from netra_api.session.dialogue import DialogueEntry, DialogueLog
from netra_api.session.state import SessionState

DIALOGUE_FETCH_LIMIT = 40
RECENT_DIALOGUE_MAX_ENTRIES = 12
RECENT_DIALOGUE_MAX_CHARS = 6000
SUMMARY_MAX_ITEMS = 5
SUMMARY_ITEM_CHARS = 120
EVIDENCE_PROMPT_MAX_CHARS = 12000


def load_coordinator_instructions() -> str:
    return resources.files("netra_api.coordinator").joinpath("prompts/coordinator.md").read_text(encoding="utf-8")


@dataclass(frozen=True)
class SelectedContext:
    canonical: dict[str, Optional[str]]
    recent: tuple[DialogueEntry, ...]
    summary: Optional[str]
    compacted_count: int


def canonical_facts(state: SessionState) -> dict[str, Optional[str]]:
    position = state.reading_position
    pending = state.pending_question
    return {
        "interaction_mode": state.interaction_mode.value,
        "source_version_id": position.source_version_id,
        "block_id": position.current_block_id,
        "sentence_id": position.current_sentence_id,
        "last_acknowledged_sentence_id": state.last_playback_ack.sentence_id if state.last_playback_ack else None,
        "active_lesson_id": str(state.active_lesson.lesson_id) if state.active_lesson else None,
        "pending_question_id": pending.question_id if pending else None,
        "pending_question_version": str(pending.question_version) if pending else None,
        "pending_question_hints_used": str(pending.hints_used) if pending else None,
        "session_version": str(state.session_version),
    }


class ContextSelector:
    def __init__(self, dialogue: Optional[DialogueLog]) -> None:
        self._dialogue = dialogue

    async def select(self, state: SessionState, trace: Optional[TurnTrace] = None) -> SelectedContext:
        entries: list[DialogueEntry] = []
        if self._dialogue is not None:
            entries = await self._dialogue.recent(state.session_id, DIALOGUE_FETCH_LIMIT)

        recent: list[DialogueEntry] = []
        used = 0
        for entry in reversed(entries):
            if len(recent) >= RECENT_DIALOGUE_MAX_ENTRIES or used + len(entry.content) > RECENT_DIALOGUE_MAX_CHARS:
                break
            recent.append(entry)
            used += len(entry.content)
        recent.reverse()

        older = entries[: len(entries) - len(recent)]
        summary = None
        if older:
            questions = [entry.content for entry in older if entry.role == "student"][-SUMMARY_MAX_ITEMS:]
            if questions:
                summary = "Earlier the student asked: " + "; ".join(
                    question[:SUMMARY_ITEM_CHARS] for question in questions
                )
            if trace is not None:
                trace.record("context_compacted", compacted_entries=len(older), kept_entries=len(recent))
        return SelectedContext(canonical_facts(state), tuple(recent), summary, len(older))


def _untrusted(text: str) -> str:
    return html.escape(text, quote=False)


def render_prompt(
    *,
    instructions: str,
    utterance: str,
    selected: SelectedContext,
    ledger: EvidenceLedger,
    feedback: list[str],
    tools: list[ToolDefinition],
    can_delegate: bool,
    remaining_model_decisions: int,
    remaining_tool_calls: int,
) -> str:
    lines = [instructions.strip(), "", "CANONICAL FACTS (authoritative; not summarized):"]
    lines += [f"- {key}: {value if value is not None else 'none'}" for key, value in selected.canonical.items()]

    lines += ["", f"BUDGET: model decisions left {remaining_model_decisions}, tool calls left {remaining_tool_calls}."]
    lines.append("TUTOR DELEGATION: " + ("available" if can_delegate else "unavailable in this build; do not delegate"))
    lines.append("PERMITTED TOOLS: " + (", ".join(tool.name for tool in tools) if tools else "none"))

    if selected.summary:
        lines += ["", "OLDER DIALOGUE SUMMARY (non-authoritative):", f"<untrusted_dialogue>{_untrusted(selected.summary)}</untrusted_dialogue>"]
    if selected.recent:
        lines += ["", "RECENT DIALOGUE:"]
        for entry in selected.recent:
            lines.append(f'<untrusted_dialogue role="{entry.role}">{_untrusted(entry.content)}</untrusted_dialogue>')

    lines += ["", "REQUIREMENTS:"]
    if ledger.requirements:
        for state in ledger.requirements.values():
            detail = f"- {state.requirement.requirement_id}: {_untrusted(state.requirement.description)} [{state.status}"
            if state.gap_code:
                detail += f", {state.gap_code}"
            if state.approximate:
                detail += ", approximate"
            lines.append(detail + "]")
    else:
        lines.append("- none declared yet")

    lines += ["", "VALIDATED EVIDENCE:"]
    budget = EVIDENCE_PROMPT_MAX_CHARS
    if not ledger.evidence:
        lines.append("- none yet")
    for evidence in ledger.evidence.values():
        text = evidence.text[: max(0, budget)]
        budget -= len(text)
        observations = "; ".join(
            f"{obs.label}={obs.value if obs.value is not None else '?'} ({obs.source.value})" for obs in evidence.observations
        )
        lines.append(
            f'<untrusted_evidence id="{_untrusted(evidence.evidence_id)}" source_version="{_untrusted(evidence.source_version_id)}" '
            f'locator="{_untrusted(evidence.locator)}" trust="{evidence.trust.value}">'
            f"{_untrusted(text)}"
            + (f" | observations: {_untrusted(observations)}" if observations else "")
            + "</untrusted_evidence>"
        )
    if ledger.rejected_evidence_count:
        lines.append(f"- {ledger.rejected_evidence_count} candidate result(s) were not authorized and are unavailable.")

    if feedback:
        lines += ["", "FAILED CHECKS FROM THE PREVIOUS STEP:"]
        lines += [f"- {item}" for item in feedback[-6:]]

    lines += ["", "STUDENT UTTERANCE:", f"<untrusted_dialogue role=\"student\">{_untrusted(utterance)}</untrusted_dialogue>"]
    return "\n".join(lines)
