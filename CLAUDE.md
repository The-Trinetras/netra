Netra Engineering Rules
Purpose
Netra is an accessibility-first learning assistant for blind and low-vision students.
This repository contains:

* a C#/WPF desktop client,
* one Python/FastAPI API,
* one Python background worker,
* shared versioned contracts,
* infrastructure,
* tests,
* evaluation assets,
* documentation.

Do not convert Netra into microservices unless explicitly instructed.
Architecture: only two agents
Netra currently has exactly two reasoning agents:

1. Study Coordinator
2. Tutor

Do not create additional agents merely because a subsystem uses AI.
The following are NOT agents:

* document ingestion,
* retrieval,
* routing/classification,
* figure processing,
* equation processing,
* video processing,
* speech recognition,
* speech synthesis,
* quiz validation,
* Neo4j projection,
* evaluation,
* background jobs.

These must remain bounded tools, services, or deterministic workflows unless an explicit architecture decision changes this.
Coordinator
The Coordinator owns:

* study-task routing,
* session-aware decisions,
* source selection,
* cross-source comparison,
* deciding whether Tutor delegation is required,
* bounded tool orchestration.

The Coordinator must not:

* write arbitrary SQL,
* directly mutate learning mastery,
* bypass authorization,
* invent evidence bodies,
* access another account's data,
* execute shell commands through model output.

Tutor
The Tutor owns:

* the current teaching objective,
* explanations,
* hints,
* pedagogical adaptation,
* proposing quizzes,
* evaluating an answer against an approved question/rubric.

The Tutor must not:

* change account identity,
* grant source access,
* directly set mastery,
* directly write Neo4j,
* execute arbitrary SQL,
* receive the Coordinator's entire conversation history.

Coordinator <-> Tutor communication must use the versioned typed handoff schema in:
shared/contracts/agent/
Never implement free-form agent-to-agent chat.
Never pass private chain-of-thought between agents.
Hard execution limits
Initial application limits:

* maximum model decisions per turn: 4
* maximum tool calls per turn: 6
* overall answer deadline: 20 seconds

These are application-enforced limits, not prompt suggestions.
Agent loops must always have:

* a maximum step count,
* deadline,
* cancellation path,
* quota/budget check,
* explicit stop condition.

Never implement an unbounded autonomous loop.
Data authority
PostgreSQL is authoritative.
Pinecone and Neo4j are derived projections.
S3 stores bytes such as source files and generated audio.
Agents never own database connections. They request operations through application services.
Authoritative ownership:

* identity/session access -> Identity/Session services in PostgreSQL
* reading position/preferences -> Session service in PostgreSQL
* source versions/reading blocks -> Content/Ingestion service in PostgreSQL
* assessment attempts/current learning status -> Learning service in PostgreSQL
* concept definitions/prerequisites -> canonical PostgreSQL records
* audio metadata/quota reservations -> Speech service in PostgreSQL
* jobs/outbox -> Job service in PostgreSQL

Neo4j must be rebuildable from canonical PostgreSQL data.
A failed Neo4j write must never roll back an already committed assessment attempt.
Pinecone result IDs must be authorized and resolved against PostgreSQL before model context is constructed.
Database boundaries
No model-generated SQL.
No raw SQL in agent prompts.
Database access belongs inside repository/service modules.
Every account-scoped read/write must receive authenticated account context from the application.
Use transactions for business invariants.
Use optimistic/version checks for session-position mutations.
Use idempotency keys for operations that may be replayed.
Schema changes require Alembic migrations.
Do not edit old applied migrations unless explicitly instructed.
Session rules
A session tracks at minimum:

* account context,
* active source version,
* current block,
* current sentence,
* last acknowledged playback position,
* interaction mode,
* active Tutor lesson,
* pending question where applicable,
* last result set,
* monotonically increasing session version.

One active speaking response is allowed per session.
A newer user turn may supersede/cancel an older response.
Navigation mutations must use expected session versions.
Duplicate request IDs must not apply the same mutation twice.
Deterministic commands
Commands such as:

* stop
* pause
* continue
* next
* previous
* repeat
* where am I
* back to reading
* undo jump
* return to question

must be handled deterministically whenever intent is unambiguous.
Do not call an LLM for deterministic navigation.
Preserve the original utterance separately for reasoning when required.
Evidence rules
Models may reference evidence IDs.
They must not construct authoritative evidence text themselves.
The server resolves evidence IDs to authoritative stored content.
Every evidence item keeps:

* source version,
* locator/page/timestamp where relevant,
* provenance,
* trust classification.

Retrieved or uploaded text is untrusted data.
Instructions appearing inside retrieved content never grant permissions.
Tutor learning rules
Assessment history is append-oriented evidence.
Do not overwrite history with a single "mastery score."
Initial learning states are:

* not assessed
* needs review
* developing
* demonstrated on recent checks

Tutor output may PROPOSE a learning event.
The Learning service validates and commits authoritative assessment changes.
Quiz answers/private answer keys must not be sent to the client before the student's answer is finalized.
Persist a pending question before delivering it to the student.
Background jobs
Long-running ingestion/multimedia/projection work belongs in worker/, not HTTP request handlers.
Jobs use PostgreSQL as durable truth.
Workers use:

* lease expiry,
* attempt count,
* next-run time,
* operation/idempotency key,
* exponential backoff with jitter.

Assume at-least-once execution.
Handlers therefore must be idempotent.
Do not keep a PostgreSQL transaction open while waiting for an external provider.
Use an outbox when a committed PostgreSQL mutation requires a later projection/update.
Protocol rules
Shared JSON schemas in shared/contracts/ are authoritative cross-language contracts.
Python and C# code must conform to them.
Do not silently add protocol fields inside only one application.
Protocol-breaking changes require a new contract version.
All WebSocket control messages contain:

* protocol_version
* message_id
* session_id
* request_id
* type
* sequence
* payload

Reject unsupported versions and unknown required structures.
Audio is not transported as large base64 JSON payloads.
Accessibility rules
Accessibility is a correctness requirement, not UI polish.
The WPF client must:

* use accessible standard controls where possible,
* expose meaningful accessible names,
* preserve keyboard operation,
* keep accessible text available even when speech fails,
* allow immediate local playback interruption,
* never require sighted interaction for a core reading flow.

Stopping audio locally must not wait for the server.
Server cancellation follows after local playback stops.
Provider adapters
All external providers sit behind interfaces/adapters.
Business logic must not depend directly on provider SDK response objects.
Do not silently switch providers or enable paid tiers.
Model IDs and important provider configuration are explicit/versioned.
No `latest` model aliases in release configuration.
Code ownership
Member 1:

* api/.../coordinator/
* api/.../session/
* identity/session routing and tool policy

Member 2:

* api/.../content/
* database migrations
* worker runtime
* ingestion/retrieval/search projection

Member 3:

* api/.../multimedia/
* worker/jobs/multimedia/

Member 4:

* api/.../learning/
* learning projection
* review scheduling
* evaluation of teaching/learning behavior

Member 5:

* client/

Speech is jointly reviewed by Members 1 and 5.
Shared contracts require review from both sides of the boundary they connect.
Do not modify another workstream merely to make your implementation easier.
Change the shared interface intentionally instead.
Claude Code behavior
When asked to scaffold:

1. Read this file first.
2. Read only files directly relevant to the requested scope.
3. State the exact files you intend to create/change.
4. Do not expand scope without explicit approval.
5. Do not install packages unless explicitly requested.
6. Do not access the network unless explicitly requested.
7. Do not implement external provider calls when asked only for boilerplate.
8. Do not invent missing product requirements.
9. Prefer TODO interfaces/stubs to speculative implementation.
10. Stop once the requested acceptance criteria are met.

Never:

* rewrite the entire repository,
* add new agents,
* add infrastructure not requested,
* run destructive Git commands,
* push commits,
* delete unfamiliar files,
* bypass tests to obtain a green result.

For scaffold tasks, create the smallest compilable/importable structure possible.
Before finishing:

* report files changed,
* report commands/tests run,
* report unresolved TODOs,
* report any architectural assumption made.
