---
paths:
  - "api/**/coordinator/**"
  - "api/**/session/**"
  - "api/**/identity/**"
  - "api/**/platform/**"
  - "api/**/transport/**"
  - "api/**/speech/**"
  - "shared/contracts/agent/**"
  - "shared/contracts/protocol/**"
---

# Coordinator and session rules

Owner: M1 System Lead.
Read [current scope](../../docs/architecture/current-scope.md), [ownership](../../docs/team/ownership.md)
and the corresponding M1–M5 guide before implementation. These rules share the
canonical authorities used by root AGENTS.md and CLAUDE.md.
Apply CLAUDE.md and the authoritative runtime baseline and contracts.
These patterns do not define new repository directories or authorize scope expansion.

Coverage note: platform/ holds the authenticated context, idempotency and
shared errors; transport/ enforces the protocol; speech/ is the server
side of the audio path. All three are M1-owned and previously matched no
rule at all. Speech and session integration additionally requires M5
review, and shared/contracts/protocol/ requires review by both owners.

## Boundaries

Coordinator owns study-task decisions, source selection, comparison and handoff.
Session service owns canonical session state and reading-position mutations.
Identity services/runtime establish authenticated account and access context.
The model cannot choose identity, grant access or directly mutate session records.
Tutor owns teaching decisions; Learning service owns validated learning writes.
Do not implement their responsibilities inside Coordinator.

## Deterministic routing

Route unambiguous control commands before model reasoning:
stop, pause, continue, next, previous, repeat, where am I,
back to reading, undo jump, return to question.

Normalize command punctuation/whitespace without rewriting the original utterance.
Never execute a command from an interim ASR transcript.
Deduplicate accepted final transcripts using the existing protocol identifiers.
Ambiguous utterances require contextual resolution or clarification.
Ambiguity must not expand tool permissions.

Stop cancels output and retains acknowledged position.
Pause preserves eligible unfinished playback.
Continue resumes eligible unfinished content; otherwise advances as specified.
Continue must never reactivate a cancelled or superseded generation.
Next/previous move by the selected navigation unit.
Repeat follows the current interaction mode.
Where am I reports orientation without moving the reading position.
Back to reading and undo jump restore their recorded return positions.
Return to question restores the existing pending question, not a newly generated one.

## State and concurrency

Use existing schema names for all state concepts; do not invent wire fields.
Track account context, source version, block, sentence, acknowledged playback,
interaction mode, connection state, active lesson, pending question, result set
and monotonically increasing session version.
Keep lesson/interaction state independent of connection state.
Bind an active source session to its selected immutable document version.
An updated document must not silently move an existing session.

Apply position/state mutations through expected-version checks.
Persist replay-safe operation identity with the committed result.
Return the prior result for a duplicate operation; do not apply it again.
Keep result ordering stable so references such as "the second one" stay meaningful.
Coordinate one active speaking response and invalidate superseded output.
Reconnection must reconcile state without reviving cancelled output.

## Reasoning and budgets

Enforce a shared originating-turn budget in application code:
- Maximum 4 attempted model decisions.
- Maximum 6 tool invocations, including redispatched/retried tool calls.
- Maximum 20 seconds to the answer deadline.

Count dispatches atomically, including concurrent tool calls.
Fallback model attempts count against the same model-decision budget.
Handoff and delegated work consume the same originating budgets and deadline.
Do not reset budgets in a nested graph, retry, fallback or resumed execution.
Waiting for a later student answer ends the current answer turn.
A later accepted student turn receives its own budget; replay does not.

Check cancellation, remaining time, permissions and quota before dispatch.
Cap tool timeouts by remaining turn time.
On exhaustion, produce the specified bounded failure/partial-result response.
Do not continue answer generation secretly after the deadline.
Return job identity for long-running authorized work.

## Tools and handoff

Expose only tools permitted for the active agent and context.
Runtime injects trusted identity; model arguments contain no authority override.
Validate arguments, results, size bounds and evidence authorization.
Use shared/contracts/agent/ for both handoff directions.
Pass evidence references, original utterance and bounded relevant dialogue.
Do not forward entire histories, credentials, arbitrary role messages or reasoning.
Resolve evidence through services; never trust an agent-supplied evidence body.
Preserve source version, provenance and trust classification across handoff.
Keep provider automatic tool execution from bypassing Netra's tool gateway.

## Verification focus

Use available fixtures to check deterministic routing, budget exhaustion,
fallback accounting, duplicate requests, version conflicts, typed handoffs,
stale output rejection and cancellation.
Include pending-question restoration and source-version pinning.
Live provider calls require explicit authorization.
Report any missing contract needed to implement these behaviours.

## Evidence repair and context

Inspect retrieved evidence for the actual question. When insufficient, record the
gap and change strategy; validate new evidence before answering. A sufficient first
result needs no extra retrieval. If repair cannot help, clarify or state the gap.
Select relevant evidence/history for each agent; compact dialogue without losing
exact canonical positions, pending questions, versions or assistance records.
The AgentSpec 8/12/45 and two-revision limits are proposals; preserve 4/6/20 until
reviewed alignment. Legacy learning-status/review handoff fields need M1/M4 contract
coordination; product changes do not authorize altering schemas or inventing enums.

## Arize AX integration

Follow the [AX plan](../../docs/architecture/arize-ax-integration.md). M1 owns the
common tracing interface, composition, allowlist/redaction, context isolation,
bounded background export and shutdown. Preserve response/cancellation/budget
semantics during AX failure; expose dropped spans and reconcile evaluation traces
with M4. Review pins/job links with M2 and real client overhead with M5. No per-turn
network export/flush, duplicate tracing stack or third product agent. Alyx is an
engineering assistant. Complete reliability gates without high-volume machinery.
