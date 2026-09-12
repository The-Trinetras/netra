---
paths:
  - "client/**"
---

# WPF client rules

Owner: M5 Client.
Apply CLAUDE.md and the approved .NET/WPF runtime baseline.
Use the existing project structure and dependency declarations.
Do not replace WPF, add a frontend framework or move backend policy into the client.

## Protocol and ownership

shared/contracts/ is authoritative for client/backend messages.
C# models must conform to the same schemas as Python models.
Do not create an independent protocol definition or casually rename fields.
Use generation or conformance checks already established by the repository.
Contract changes require explicit approval and review with the backend owner.

Session service owns durable session state and reading position.
The client renders/reconciles that state and reports playback acknowledgements.
Local UI state and cached progress must not masquerade as a committed server write.
Keep connection state separate from interaction/learning mode.
Do not trust a local account/source/session ID as an authorization decision.
Keep authentication material in the plan's protected Windows credential path.
Never include tokens in URLs, diagnostics, public state or ordinary logs.

## Accessibility

Accessibility is functional correctness.
Prefer standard WPF controls with meaningful names and appropriate UI Automation.
Every core reading interaction must work with a keyboard and accessible text.
Preserve predictable focus, traversal order and orientation after updates.
Do not require a mouse or sighted assistance for a core reading flow.

Cooperate with NVDA; do not globally mute the screen reader or operating system.
Avoid duplicate token-by-token announcements through Netra speech and NVDA.
Keep source reading distinguishable from generated explanation.
Keep text, keyboard controls and usable status available when speech fails.
Expose real progress, failure and reduced modes without misleading reassurance.

Render multimedia structures supplied through contracts.
Do not regenerate authoritative figure labels or equation structure in the client.
Coordinate math/braille integration with M3.
Do not claim tactile usability without the necessary device/user validation.

## Input and deterministic commands

Support the contracted application commands:
stop, pause, continue, next, previous, repeat, where am I,
back to reading, undo jump, return to question.

Send navigation intent through the deterministic application path.
The client must not call an LLM to decide ordinary navigation.
Preserve original typed/spoken text when forwarding a non-command request.
Only accepted final ASR transcripts may become spoken-input turns.
Interim transcripts may support UI feedback but must never move position,
submit a quiz answer or trigger a deterministic command.
Deduplicate repeated final events according to protocol identity.

Microphone access follows the plan's explicit modes and user control.
Local voice-activity interruption is a playback safety signal, not a transcript turn.
Do not silently capture or retain microphone audio outside the selected mode.

## Playback and cancellation

STOP must halt local playback immediately without waiting for network or server.
Invalidate the active playback generation and queued stale output locally.
Then propagate cancellation using the existing protocol.
Reject late audio from cancelled/superseded requests, including after reconnect.
Cancellation must not be undone by continue, an old acknowledgement or late packets.

Pause may preserve eligible unfinished playback under the session policy.
Resuming cancelled content requires an explicit authorized action/new generation;
never silently revive the old generation.
Allow only one active speaking response per session.
Use bounded queues and backpressure; do not buffer unbounded audio.
Do not run network waits or long processing on the WPF UI thread.

Use the approved binary audio framing when defined by the protocol.
Do not invent large base64 audio envelopes or a competing audio protocol.
If required framing is absent, report the contract gap.
Acknowledge actual playback at the prescribed sentence/progress boundaries.
Distinguish downloaded, played, completed and durably acknowledged positions.
Do not skip unheard content by acknowledging receipt as completion.
Cache only complete validated segments with their access/version metadata.

## Recovery and provider boundaries

On disconnect, expose reduced mode and retain eligible authorized cached reading.
Do not claim offline position changes are saved to PostgreSQL.
Reconcile version and acknowledged position on reconnect before resuming.
Reject stale responses and preserve the existing lesson/pending question.
Maintain typed/keyboard controls when ASR fails and accessible text when TTS fails.
No silent voice/model/provider change or paid fallback.

Keep speech integrations behind approved adapters.
Server provider credentials must not be embedded in the desktop application.
Backend speech/session interfaces require M1 review; do not rewrite that workstream.
Do not add audio libraries or SDK sample dependencies without explicit approval.

## Verification focus

Run available Windows checks without implicit NuGet restore or package download.
Check keyboard-only reading, focus, NVDA output, immediate STOP and stale audio.
Check pause/continue, duplicate final transcripts, reconnect and position recovery.
Use deterministic playback/protocol fixtures for scaffold and interface work.
Report whether tests used real audio, NVDA, braille hardware or only fixtures.
Do not claim Windows accessibility tests passed from a Linux-only environment.
