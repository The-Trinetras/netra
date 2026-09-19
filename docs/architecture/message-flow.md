# Message flow

## Authority and common boundaries

Sources: [current scope](current-scope.md), historical [Engineering Plan](Netra_Final_Engineering_Plan.md) §§7–20 and Appendices A–C, [runtime baseline](runtime-baseline.md), [shared contracts](../../shared/contracts/), [CLAUDE.md](../../CLAUDE.md) and [domain rules](../../.claude/rules/). The [migration report](../audits/documentation-migration-report.md) identifies current pending work; the [Phase 10 report](../audits/phase-10-repair-report.md) is historical; it is not architecture authority or evidence that all flows are implemented.

**2026-09-12 protocol decisions** (session.snapshot / quiz.question / error payloads, canonical interaction-mode vocabulary, last-stable-result-set design, stable client request identity, and binary synthesized-audio framing) are approved and typed as of this pass. See the field/schema tables inline below and `shared/contracts/protocol/v1/{server_to_client,error,audio_frame_header}.schema.json`. Downstream endpoint/dispatcher wiring for these payloads is not yet implemented — the types exist for that wiring to consume next.

The System Lead clarified on 12 September 2026 that **Identity service owns identity verification, device access and the account–session security/lifecycle binding; Session service owns canonical mutable session state and its versioned mutations**. Identity establishes whether a principal may access the session. Session service then validates and applies permitted state changes. Both use authoritative PostgreSQL records. The word “session” does not grant Identity ownership of reading, navigation or learning interaction state.

The flows below specify approved target behaviour unless an explicit implementation note says otherwise; they are not runtime verification. All flows enforce authorization at service boundaries. Wire definitions belong to shared/contracts; schema examples in the plan do not replace committed contracts. Versioned mutations use replay-safe operation identity and expected-version checks where applicable. Read-only operations do not imply a state write. Agent/tool work is bounded by permissions, cancellation and the originating budget. Pending payloads below are described semantically only.

## 1. Authenticated session establishment

- **Initiator/path:** Each student receives a one-time access code issued by an operator or teacher. The WPF desktop exchanges it once over HTTPS for a device credential and keeps that credential in Windows Credential Manager (decision D-CRED, 20 September 2026, recorded in [integration status](../team/integration-status.md); it replaces the earlier system-browser PKCE sign-in, which may return later). API verifies credentials; authenticated WSS carries interactive traffic. HTTP handles uploads, the access-code exchange, settings and job-status operations. The exchange is `POST /v1/device-credentials` ([device_credential.schema.json](../../shared/contracts/http/v1/device_credential.schema.json)): `{access_code, request_id}` → 201 `{credential, expires_at}`; an unknown, expired, revoked or used code is one indistinguishable 401 `AUTH_REQUIRED`; a lost response is retried with the same request_id within 15 minutes, which issues a fresh credential and revokes the earlier one.
- **Authoritative checks:** Identity verifies principal/account/device access and the account–session security binding. A supplied session ID is not authorization. Tokens are validated before accepting the connection and expiry/revocation remains relevant during it.
- **Version/idempotency:** Validate supported protocol version; load canonical session version rather than accepting client state as truth. A specific establishment mutation payload/operation contract is not defined here.
- **Agents/writes:** No agent. Identity owns any identity/access persistence; Session service owns initialization of mutable session state in PostgreSQL.
- **Destination:** Verified connection context stays server-side; safe session state returns to the authorized desktop via the typed `session.snapshot` payload (reference-only: session_version, interaction_mode, active source/block/sentence, last_acknowledged_sentence_id, and references — not full content — for active_lesson/pending_question/last_result_set). Endpoint wiring to actually send it on establishment is not yet implemented.

## 2. Deterministic navigation command

- **Initiator/path:** Desktop `navigation.command`, or an unambiguous accepted final utterance routed by application logic before a model call. Commands are stop, pause, continue, next, previous, repeat, where am I, back to reading, undo jump and return to question; exact wire spellings/units come from the client contract.
- **Authoritative checks:** Verify account/session access, active source access, pinned version and valid destination. Interim ASR has no command authority.
- **Version/idempotency:** Resolve request_id against the idempotency store first (`replay_or_conflict`, tightened 2026-09-12): a genuine retry of the identical command replays its recorded result unconditionally, even if expected_session_version is now stale; a request_id reused with a *different* payload fails closed (`REQUEST_ID_CONFLICT`) rather than guessing an interpretation. Only a genuinely new request_id reaches the expected-session-version check. Competing stale mutations refresh rather than move twice.
- **Agents/writes:** No agent. Session service commits applicable position/context changes and increments session version. Orientation reads do not move position. Return to question restores the existing question.
- **Destination:** Authorized desktop receives state/control outcome and appropriate reading content, or a typed `error` payload (code/message/retryable, plus current_session_version when the code is SESSION_VERSION_CONFLICT). STOP handling is detailed in flow 7.

## 3. Normal Coordinator request

- **Initiator/path:** Accepted `turn.submit` after deterministic routing. Voice input is a completed final utterance; interim transcripts never become turns. Push-to-talk (D-MIC, 20 September 2026): the client sends `asr.start` with a `capture_id`, streams binary microphone frames (`microphone_frame_header.schema.json`: 16 kHz 16-bit little-endian mono PCM, contiguous `sequence`, `end_of_utterance` on release, at most 1 s per frame and 60 s per capture), and receives `asr.transcript` messages under the `asr.start` request_id. The server only transcribes; the client submits the one final transcript as an ordinary `turn.submit` with `input_mode: voice`, reusing one request_id per `transcript_id`. An empty final means nothing was recognised. An unreadable frame header closes the connection with 1007; any other frame fault abandons the capture with an `INVALID_REQUEST` error.
- **Authoritative checks:** Validate contract, trusted session context, expected session version, source permissions and tool policy. PostgreSQL validates vector matches before evidence enters model context; retrieved instructions remain untrusted data.
- **Version/idempotency:** Correlate the existing request identity with the turn and replay record. Fence superseded output. The same turn shares 4 model decisions, 6 total tool calls and a 20-second deadline across retries, fallback and delegation. Client request-identity lifecycle (approved 2026-09-12): request_id is minted once per logical user action (a submit, an accepted final utterance, one navigation command) and reused verbatim if that same action must be retransmitted after a dropped connection, before any acknowledgement was seen; message_id and sequence are fresh per transmitted frame. A response correlating to a pending request_id (success or error alike) ends that action — a further client action mints a new request_id. This applies identically to `turn.submit` and to deterministic navigation commands (flow 2).
- **Agents/writes:** Coordinator selects bounded tools, answers, clarifies or hands teaching to Tutor. Application services own turn/session writes; LangGraph owns execution checkpoint mechanics. Search alone is not an assessment write.
- **Destination:** Validated public content goes to response delivery, or an authorized long operation returns job identity. Failure uses the typed `error` payload (see flow 2's version-conflict handling for one case of it); dispatcher/endpoint wiring to actually route `turn.submit` end-to-end is not yet implemented.

## 4. Coordinator → Tutor handoff

- **Initiator/path:** Coordinator requests `delegate_to_tutor`; application validates the Coordinator-to-Tutor schema before Tutor execution.
- **Authoritative checks:** Validate authorized lesson context, target concepts and evidence references; resolve source text server-side. Pass original utterance, bounded dialogue and the existing `assessment_summaries` field (legacy status-shaped items; factual history requires coordinated contract work), not unrestricted history or private reasoning.
- **Version/idempotency:** Preserve contract handoff/request/session/lesson correlation, source/evidence versions and deadline. Handoff/delegated work shares the originating counters and deadline; no fresh budget on replay.
- **Agents/writes:** Both agents participate through the typed boundary only. Application-managed checkpoints retain execution continuity; Session service owns active lesson/return context, not the model.
- **Destination:** Tutor receives the validated handoff. The contract's teaching modes do not define the pending session interaction-mode vocabulary.

## 5. Tutor → Coordinator result/proposal

- **Initiator/path:** Tutor returns the approved result schema: bounded public segments, evidence references, optional pending-question reference and learning-event proposals.
- **Authoritative checks:** Validate handoff correlation, current authorized context and evidence. Learning service verifies proposal/question/attempt/rubric/finality before committing. Public response formatting excludes private answers and grading material.
- **Version/idempotency:** Preserve question/evidence versions and replay-safe operation identity. Replayed proposals must not append another attempt. Persist the pending question before delivery.
- **Agents/writes:** Tutor proposes; Coordinator receives. Learning service commits validated assessment history and outbox atomically; Session service owns pending session context. No automatic learning-status derivation or review scheduling is required. Record delivered activity, stated reasoning, feedback and assistance through validated services; complete representation is an integration gap.
- **Destination:** Coordinator/application delivery receives public content; Learning service receives proposals. Client `quiz.question` payload (approved 2026-09-12) is the wire form of `StudentFacingQuestion` — question_id, question_version, kind, prompt, options, hints_used; structurally excludes answer_key/rubric/grading notes, which exist only server-side. A spoken/typed answer is an ordinary `turn.submit`, not a separate message type. Optional-check grounding criteria: **Pending decision.** Learning-label thresholds and review intervals are removed requirements, not pending values.

## 6. Response streaming to client

- **Initiator/path:** Application response formatter sends validated public `response.segment` content to WPF and permitted text to Speech service/ElevenLabs.
- **Authoritative checks:** Confirm authorized evidence/content and current output eligibility. Speech checks cache access and reserves quota before fresh synthesis (D-QUOTA: 20,000 characters per student per UTC day). When the day's quota is used up, the rest of that reply is text only and, once per connection, the server sends an `error` notice with the reply's request_id: `RESOURCE_UNAVAILABLE`, `retryable: false`, `details.reason: speech_quota_exhausted`. It does not fail the request, whose text was already delivered. Never narrate a full tool/model object or private assessment fields.
- **Version/idempotency:** Use approved generation/segment/sentence correlation and ordering. One active speaking response; bounded queues apply backpressure. Cache identity includes access scope and synthesis configuration; incomplete cancelled audio is not a completed cache entry.
- **Agents/writes:** Agents may produce public text, but delivery and speech are services. Speech service writes metadata/reservations in PostgreSQL and completed audio to private S3 as appropriate. Sending audio does not advance played position.
- **Destination:** Desktop accessible text and local playback. Binary audio framing (approved 2026-09-12, server-to-client only — microphone upload keeps its own approved protocol): `[4-byte big-endian header length][UTF-8 JSON header][raw audio bytes]`, header version/generation_id/segment_id/sequence/end_of_segment/end_of_generation/media_type, 16 KiB header cap, unknown fields and wrong types rejected, declared length validated against actual frame bytes before parsing. No authoritative total-message-size limit exists yet — that remains an explicit open transport-hardening decision. Source-reading versus generated-explanation distinction must remain visible/audible without inventing a new segment-kind value.

## 7. Cancellation / STOP fencing

- **Initiator/path:** Local STOP input immediately halts and flushes playback and invalidates current output, then sends `response.cancel`. Push-to-talk/press-to-interrupt pauses current speech; hands-free VAD interruption and echo cancellation are deferred. New turns may supersede current output.
- **Authoritative checks:** Backend validates caller/session and cancellation target. Service dispatch and response delivery check cancellation. Local stopping never waits for the network or a model.
- **Version/idempotency:** Cancelled/superseded generations remain ineligible for playback, including late arrivals and reconnect. Cancellation propagates to generation, TTS and queued work where supported; replay cannot restore output. Binary frame-to-generation binding (approved 2026-09-12): a frame is eligible only for the generation the client has *explicitly admitted* (never a side effect of merely receiving a frame with that generation_id); admission is fenced by the same cancellation set STOP uses, and disconnect fences whatever generation was active exactly as STOP does, without sending `response.cancel` (no connection to send it on). Per-generation sequence numbers must strictly increase — duplicates/decreases rejected, gaps permitted.
- **Agents/writes:** No reasoning is needed to stop. Application records cancellation/turn state and retains acknowledged position through Session service; providers may already have consumed quota.
- **Destination:** Local silence is immediate; backend control affects outstanding execution/delivery. Pause may preserve eligible unfinished playback. Continue never resurrects a cancelled generation; later authorized playback must respect the cancellation boundary. Failure uses the typed `error` payload (approved 2026-09-12): code (closed vocabulary), message (safe/accessible text only), retryable, and current_session_version when applicable — never a stack trace, SQL detail, or provider payload.

## 8. Playback acknowledgement

- **Initiator/path:** WPF player sends `playback.ack` for actual playback, including sentence completion and progress within long segments.
- **Authoritative checks:** Validate session access and that generation, segment and sentence refer to eligible delivered content. Download completion is not playback evidence.
- **Version/idempotency:** Correlate and deduplicate acknowledgements; prevent stale output from moving position. Session service serializes applicable position changes under canonical version semantics; do not add an expected-version field absent from the acknowledgement contract.
- **Agents/writes:** No agent. Session service persists acknowledged playback/reading progress and applicable session revision in PostgreSQL.
- **Destination:** Authoritative position supports later resume; client state reconciliation uses the session response. Snapshot representation is the approved reference-only `session.snapshot` in `server_to_client.schema.json`; endpoint wiring remains pending.

## 9. Ingestion job flow

- **Initiator/path:** Authorized upload/source selection, directly through the application or a permitted Coordinator tool, creates durable work. Worker performs fetch, parse, validate and index stages.
- **Authoritative checks:** Source ownership/access, safe address/file constraints, quota and permitted provider input. Validate blocks/locations and required capabilities before source activation.
- **Version/idempotency:** Deduplicate source hash/parser configuration and operation identity. Claim/renew PostgreSQL leases; lost lease cannot commit as current owner. Record completed stages and remote operation IDs; reconcile uncertain external completion before repeating a call.
- **Agents/writes:** Coordinator may request work; ingestion itself is a deterministic workflow. PostgreSQL stores jobs, immutable source metadata, validated blocks and outbox; private S3 stores source bytes. External work runs outside long DB transactions.
- **Destination:** Initiating client receives job identity/status; resulting ready source is available only after activation gates. Existing sessions stay pinned. Exact job contract and any unspecified status/completion payload: **Pending approved contract definition**.

## 10. Projection / outbox flow

- **Initiator/path:** Owning service commits a canonical mutation and outbox record in one PostgreSQL transaction. Worker delivers derived updates to Pinecone or Neo4j.
- **Authoritative checks:** Read canonical entity/source state and scope; projection never changes assessment or access authority. Worker verifies its current lease before committing job ownership effects.
- **Version/idempotency:** At-least-once execution with retry/backoff and jitter. Stable operation/entity identity and event/source versions make replays safe and prevent older events overwriting newer projections. Acknowledge only after the effect is safely recorded.
- **Agents/writes:** No agent. Canonical PostgreSQL writes precede derived upserts; worker updates job/outbox progress. Projection retry does not create a fresh assessment attempt.
- **Destination:** Derived search/graph stores and durable delivery status. Failure retains recoverable work; authorized PostgreSQL reduced paths remain available as specified. External job-schema necessity/shape: **Pending approved contract/policy decision.**

## 11. Reconnect / session resume

- **Initiator/path:** Desktop reconnects with backoff and sends approved `session.resume` information.
- **Authoritative checks:** Identity re-establishes the account–session access binding; Session service reconciles canonical state and acknowledged position. Recheck source access and pinned version; client cached state is not authority.
- **Version/idempotency:** Reconcile last-known session version and acknowledgement using the current contract. Preserve pending question, stable result context and cancellation fencing. Do not restart a cancelled generation, repeat navigation or create a replacement quiz merely because transport resumed.
- **Agents/writes:** No agent is needed for reconnect. Session service owns any validated reconciliation writes. A later accepted teaching turn may resume Tutor execution with normal checks; connection state stays separate from learning state.
- **Destination:** Authorized desktop receives the typed `session.snapshot` and a sentence-boundary resume opportunity. When `pending_question` is non-null, the server also sends a fresh `quiz.question` with the same persisted question_id/version immediately after the snapshot, so the client need not separately re-fetch it. Client retry request-ID lifecycle (see flow 3) applies unchanged across reconnect: a still-unacknowledged `turn.submit`/navigation command retries under its original request_id, not a new one.

## Last stable result set (approved 2026-09-12)

`SessionState.last_result_set` and the `session.snapshot` field of the same name carry only `{result_set_id, created_at}` — a reference, never the result list itself. The approved design places the ordered list (bounded to 10 items by `session/result_sets.py`, each `{ordinal, evidence_id, label}`) in a new table under the Session service's existing ownership of "stable last result set." "Open the third one" resolves `result_set_id` → stored row → `items[2].evidence_id` → the normal evidence-authorization path (flow 3), which independently re-checks access, deletion and source-version compatibility every time — a result set surviving longer than the evidence it points to must not bypass that check. A newer completed search replaces the session's reference; referencing an expired or superseded result set returns the typed `error` payload (`STALE_REQUEST`/`RESOURCE_UNAVAILABLE`), never a silent resolution against a different list.

The result-set model/repository protocol exists in [result_sets.py](../../api/src/netra_api/session/result_sets.py); the concrete PostgreSQL repository/table migration remains pending.

## Resolved 2026-09-12

| Item | Resolution |
|---|---|
| `session.snapshot`, `quiz.question`, `error` payloads | Typed sub-schemas of `server_to_client.schema.json` / `error.schema.json`; see flows 1, 3, 5, 7, 11 above |
| Stable last-result-set representation | Reference-only in session state; ordered list stored server-side — see "Last stable result set" above |
| Canonical session interaction-mode vocabulary | `idle`, `reading`, `tutor_lesson`, `quiz` — separate from ConnectionState and from playback state; see [data ownership](data-ownership.md) |
| Binary audio encoding/framing and generation metadata binding | `audio_frame_header.schema.json`; server-to-client only — see flows 6–7 above |
| Client retry request identity lifecycle | request_id stable per logical action, reused on retransmit; message_id/sequence fresh per frame — see flow 3 above |
| Microphone upload protocol (D-MIC, 20 September 2026) | `asr.start`, `microphone_frame_header.schema.json`, `asr.transcript` — see flow 3 above; served by `Connection.handle_bytes` and the Deepgram adapter (B1) when `NETRA_DEEPGRAM_API_KEY` and `NETRA_DEEPGRAM_MODEL` are set; STOP or a new press ends a capture that is still receiving audio |
| Exact Coordinator Gemini model ID | `gemini-3.8-flash` (approved configured pin, not verified provider availability) |
| S3 SDK/version | `boto3==1.43.92` (approved pin; `uv.lock` regeneration blocked, `uv` not installed) |

## Unresolved decisions retained

| Item | Status |
|---|---|
| Python/C# contract mirror package location | Pending approved contract/policy decision. |
| Need for, and shape of, external job contract; current job schema is empty | Pending approved contract/policy decision. |
| Optional-check grounding criteria and factual activity/answer/reasoning/assistance representation | M4 with M1/M2: define validation and coordinate any contract/schema migration; no automatic labels or review intervals. |
| AgentSpec 8/12/45 budget and two-revision proposal | M1 with M3/M4: pending alignment; existing 4/6/20 remains approved. |
| Authoritative total binary-message size limit (the 16 KiB *header* bound is set; no total-frame limit exists anywhere in the runtime baseline or committed contracts) | Pending approved contract/policy decision. |
| Downstream endpoint/dispatcher wiring for the newly-typed server payloads | Pending implementation. |
| Retention values | Pending product decision; payload wiring is implementation coordination, not an undefined-schema blocker. |

This document does not settle the still-pending decisions through examples, enum choices, adapter implementations or existing stubs. No wire fields, endpoints or message types beyond what is now committed in shared/contracts/ are defined here.

## Current study journey integration gaps

### Trace correlation without protocol changes

Follow the [AX integration plan](arize-ax-integration.md). M1 propagates tracing
context through reviewed internal calls; M2 reviews asynchronous job correlation.
Trace/span IDs and execution attempts are diagnostic identities, separate from the
stable logical `request_id`, fresh frame `message_id` and canonical session version.
Export must not change replay, cancellation, budgets or acknowledgement semantics.
Do not add AX keys or direct AX calls to WPF. M5 joins permitted local measurements
through existing request/generation identities; any new transport fields require
M1/M5 contract review. Measure local STOP-to-silence with one monotonic clock,
not by subtracting client/server timestamps. Background export cannot delay output.

### Product integration

Coordinator must inspect evidence, record gaps, change retrieval strategy where useful
and validate the result before Tutor explanation. Sufficient first evidence needs
no repair; unresolved evidence produces clarification or a stated limitation.
This is target behaviour, not proof that the stubbed loops execute it.

The v1 handoffs still carry legacy assessment status and review event vocabulary.
Do not rename it or encode untested study as a new enum. M1/M4 own contract alignment;
M2 owns persistence mechanics and M5 consumes the reviewed public representation.
Optional questions retain the existing pending-question/reconnect semantics above.

M3/M5 must integrate selected-video playback identity and actual time with evidence
resolution, then separately validate playback access and analysis permission/ability.
Existing timestamp models do not establish a client playback-control protocol.
The web-view dependency and any missing shared payload need coordinated approval.
