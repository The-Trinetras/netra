# Netra architecture overview

## Authority and scope

This document describes the approved architecture, not the completeness of its implementation. Product authority comes from [current scope](current-scope.md) and the supplied [AgentSpec](Netra-SPEC.md); preserved architecture is read alongside the historical [Engineering Plan](Netra_Final_Engineering_Plan.md), [runtime baseline](runtime-baseline.md), [shared contracts](../../shared/contracts/), [CLAUDE.md](../../CLAUDE.md), and [domain rules](../../.claude/rules/). The System Lead clarified the Identity/Session distinction on 12 September 2026; see [data ownership](data-ownership.md).

Netra has exactly two agents: **Coordinator** and **Tutor**. Team workstreams do not define agent count. Services below are application responsibilities, not additional deployable microservices.

## Components (approved target responsibilities)

| Component | Role and execution path |
|---|---|
| C# WPF desktop | Keyboard, accessible text, NVDA integration, microphone input, speech playback, immediate local STOP, and playback acknowledgement. Accessibility is functional correctness. |
| Python FastAPI API | Authenticated HTTP/WebSocket boundary; deterministic routing; session, tool and response control. Runs interactive requests. |
| Coordinator | Bounded study-task reasoning, source selection, comparison and typed Tutor handoff through its Gemini adapter. |
| Tutor | Bounded teaching decisions and private lesson state through its Groq adapter; proposes learning events. |
| LangGraph | Orchestrates agent execution and PostgreSQL checkpoints. A checkpoint is execution state, not authority to bypass service validation or replay protection. |
| Worker process | Executes leased PostgreSQL jobs for ingestion, multimedia processing and projections. Legacy review-scheduler code is outside current product requirements. Separate from the API process. |
| Ingestion and retrieval | Prepare immutable source versions and navigation blocks; retrieve bounded, authorized evidence. Search chunks map back to reading blocks. |
| Multimedia | Produces source-linked figure, diagram, equation and video evidence with explicit uncertainty. These are tools/workflows, not agents. |
| Learning | Validates and commits factual study activity, optional-check answers, stated reasoning, feedback and assistance; maintains derived Neo4j projection boundaries. Automatic labels and review scheduling are removed requirements. |
| Speech | Deepgram streaming recognition and ElevenLabs synthesis behind adapters; public-content formatting, quota reservations and completed audio caching. |

## Storage and provider boundaries

| System | Architectural role |
|---|---|
| PostgreSQL | Authoritative structured state, access records, reading position, assessments, source metadata, durable jobs and outbox; also LangGraph checkpoints. |
| Private S3 | Authoritative stored source bytes and durable audio objects; PostgreSQL records ownership and object/version references. |
| Pinecone | Derived semantic-search projection; returned references must pass PostgreSQL authorization and source-version checks. |
| Neo4j | Derived, rebuildable concept/learning graph; cannot overwrite canonical records. |
| Redis | Optional cache only; not required for correctness or recovery. |

Gemini (`gemini-3.8-flash`, approved configured pin, 2026-09-12 — not an independently verified provider availability claim) serves the Coordinator and configured text embeddings; Groq serves the Tutor. Twelve Labs Marengo retrieves media and Pegasus describes selected evidence. Tavily remains the discovery adapter choice for YouTube search; general web extraction and Drive are deferred. LlamaParse remains the PDF parsing choice with permitted Tesseract OCR recovery. S3 access uses boto3==1.43.92 (approved pin, 2026-09-12; declared in the authoritative manifest, `uv.lock` regeneration blocked because `uv` is not installed in this environment — see runtime-baseline.md). Integrations remain behind adapters, with no silent provider/model substitution. Exact dependencies and runtime choices remain in the runtime baseline.

## Interactive request path

The desktop establishes authenticated access. The API validates identity, session access and the applicable contract. Exact navigation/control commands route directly to deterministic application logic. Other accepted turns enter bounded Coordinator execution; teaching uses a typed Tutor handoff. Services enforce permissions, resolve evidence against PostgreSQL and own all authoritative writes. Validated public response segments return as accessible text and, when available, speech. The client acknowledges actual playback rather than download completion.

Coordinator execution shares a maximum of 4 model decisions, 6 total tool calls and a 20-second answer deadline across retries, fallback and delegated work. Long ingestion work returns a job identity instead of extending this answer loop.

## Background path

Authorized ingestion creates durable PostgreSQL job records. A worker claims a lease in a short transaction, commits, then performs bounded external work. It records stage results and remote operation identities for recovery. Validated content and outbox events are committed in PostgreSQL; projection work updates Pinecone/Neo4j idempotently. Required validation/indexing gates control activation of a source version. Existing reading sessions remain pinned to their selected version.

Jobs execute at least once, using lease recovery and retry/backoff. External calls do not occur inside long database transactions. Projection failure leaves canonical records intact and pending work recoverable.

## Deployment and reduced operation

Initial API/worker deployment remains Docker Compose on one EC2 instance. The historical plan places PostgreSQL there; the AgentSpec targets RDS/PgBouncer. The Compose file is empty, so database placement/pooling remains an explicit M1/M2 alignment decision. This is a shared host failure boundary, not a high-availability claim. API and worker use the same repository-defined Python baseline.

Pinecone failure permits the specified authorized PostgreSQL text-search reduced mode; Neo4j failure does not erase learning history. Speech failure preserves available text, keyboard access and authorized cached audio. PostgreSQL failure prevents new durable mutations; local progress must not be represented as synchronized.

## Pending definitions

Coordinator Gemini model ID (`gemini-3.8-flash`) and S3 SDK selection (`boto3==1.43.92`) are now approved configured pins (2026-09-12); `uv.lock` regeneration for the latter remains blocked pending `uv` installation. `session.snapshot`, `quiz.question`, `error`, the canonical interaction-mode vocabulary, the last-stable-result-set design, binary synthesized-audio framing and stable client request identity are now approved and typed — see [message flow](message-flow.md) and [agent boundaries](agent-boundaries.md).

Still pending: the three server payloads' downstream endpoint/dispatcher wiring, the neutral Python/C# contract-mirror package location, the job-contract necessity/shape, and grounding criteria for optional checks. Automatic learning labels and review scheduling no longer require policy values. Factual-history contract migration, proposed budgets and the desktop video dependency remain open; see the [migration report](../audits/documentation-migration-report.md). This overview does not approve alternatives discussed in the plan or assert that scaffolds implement the full architecture.

Related: [agent boundaries](agent-boundaries.md), [data ownership](data-ownership.md), [message flow](message-flow.md).

## Target behaviour and current evidence

The target is the evidence-gap repair loop and exact return to study described in
[current scope](current-scope.md). A sufficient first result needs no extra retrieval.
Context selection and compaction preserve canonical facts outside summaries.
Video playback and analysis require separate readiness checks.

The Coordinator and Tutor turn loops raise `NotImplementedError`; the WebSocket
endpoint/dispatcher are empty. Typed structures and local implementations exist,
but no integrated journey or live provider/accessibility result was verified by
this migration. See the [owner gap register](../audits/documentation-migration-report.md).
