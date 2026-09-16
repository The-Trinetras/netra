# M1–M5 ownership and implementation entry points

Start with [current scope](../architecture/current-scope.md), the
[runtime baseline](../architecture/runtime-baseline.md) and [contracts](../../shared/contracts/).
The original Engineering Plan's P1–P5 assignments are historical; use these M1–M5
guides for current work. Ownership identifies responsibility, not permission to
edit unrelated work or bypass contract review.

Use the [M1–M5 implementation prompts](prompts/README.md) to start parallel coding.
Each prompt includes its role's deliverables and reads a shared coordination agreement.

| Workstream | Owns | Implementation guide / domain rule |
|---|---|---|
| M1 | Coordinator, identity/session context, routing, tool policy, handoffs, cancellation/version semantics; server speech/transport | [M1](M1.md) / [coordinator](../../.claude/rules/coordinator.md) |
| M2 | PostgreSQL, ingestion, reading blocks, retrieval, source versions, jobs/outbox, Pinecone projection | [M2](M2.md) / [backend-data](../../.claude/rules/backend-data.md) |
| M3 | Figures, diagrams, basic equations, visual/video evidence and adapters; table extraction with M2 storage | [M3](M3.md) / [multimedia](../../.claude/rules/multimedia.md) |
| M4 | Tutor, optional checks, factual activity/answer/reasoning/feedback/assistance history, evaluation and derived Neo4j projection | [M4](M4.md) / [learning](../../.claude/rules/learning.md) |
| M5 | WPF, accessibility, NVDA, keyboard, microphone, speech/playback and client protocol | [M5](M5.md) / [client](../../.claude/rules/client.md) |

## Shared boundaries

Paths identify actual schemas or internal models, not proposed new interfaces.
All consumers review changes to their boundary before integration.

| Boundary / owner | Authority / current definition | Consumers | Invariants and open integration work |
|---|---|---|---|
| Desktop protocol / M1 + M5 | [protocol v1](../../shared/contracts/protocol/v1/) | M4 public questions; all response producers | Stable request identity, strict payloads, version checks, local STOP and reconnect fencing; endpoint/dispatcher wiring and full client journey pending. |
| Typed handoff / M1 + M4 | [agent v1](../../shared/contracts/agent/v1/) | M2/M3 evidence; M5 public output | Bounded authorized evidence, shared originating budget, no private reasoning; legacy status/review fields require factual-history migration review. |
| Identity/session / M1 | [auth_context.py](../../api/src/netra_api/platform/auth_context.py), [state.py](../../api/src/netra_api/session/state.py), [repository.py](../../api/src/netra_api/session/repository.py) | M2–M5 | Identity owns access binding; Session owns mutable state/version and pinned position. PostgreSQL repository and complete transport integration remain pending. |
| Evidence/source/reading / M2 | [evidence.py](../../api/src/netra_api/content/retrieval/evidence.py), [blocks.py](../../api/src/netra_api/content/reading/blocks.py), [source models](../../api/src/netra_api/content/sources/models.py) | M1/M3/M4/M5 | Canonical access/version validation after retrieval; chunks map to reading blocks. Internal models are not additional wire schemas. Concrete ingestion/retrieval/persistence still needed. |
| Visual/video/table evidence / M3 with M2 storage | [multimedia models](../../api/src/netra_api/multimedia/), [video timestamps](../../api/src/netra_api/multimedia/video/timestamps.py), [reading blocks](../../api/src/netra_api/content/reading/blocks.py) | M1/M4/M5 | Observed/generated/uncertain values and source/time mapping; original-media checks, table integration and shared playback representation pending. |
| Factual history / M4 with M2 persistence | [assessment models](../../api/src/netra_api/learning/assessment/models.py), [service](../../api/src/netra_api/learning/assessment/service.py), [quiz models](../../api/src/netra_api/learning/quiz/models.py) | M1/M5 | Learning validates proposals; public/private separation; retain attempts/assistance without mastery. Full activity/reasoning/feedback representation and commits remain incomplete. |
| Jobs/outbox / M2 | [worker runtime](../../worker/src/netra_worker/runtime/), [empty job schema](../../shared/contracts/jobs/v1/job.schema.json) | M1/M3/M4 | Leases, at-least-once, idempotency, version ordering, short transactions. External schema need/shape and concrete persistence remain pending. |
| Projections / M2 Pinecone, M4 Neo4j | [Pinecone worker](../../worker/src/netra_worker/jobs/search_projection/pinecone.py), [Neo4j projection](../../api/src/netra_api/learning/graph/projection.py) | Retrieval and teaching services | Rebuildable only; cannot overwrite PostgreSQL truth. Retain stores/code while reviewing obsolete learning-policy coupling. |

## Coordination and build order

First connect a labelled fixed-evidence desktop/API journey: selection, question,
answer, STOP and exact return. Then integrate real PDF evidence, a missing-evidence
repair and optional tutoring/history. Separately validate uploaded-video and YouTube
playback and analysis readiness. Finally test recovery, duplicate requests, stale
audio and keyboard/NVDA operation using the same scenario. This is a target build
order, not current pass evidence. The AgentSpec's two-day allocation is an estimate.

M1/M4 coordinate handoffs and history; M2/M3 coordinate source identity/table storage;
M3/M5 coordinate player time and visual exploration; M1/M5 coordinate speech,
authorization, retry and cancellation. No owner may add fields, dependencies,
frameworks or services to fill an unresolved boundary silently. See the
[integration checklist](integration-checklist.md) and
[owner gap register](../audits/documentation-migration-report.md).
