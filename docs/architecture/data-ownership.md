# Data ownership

## Authority and the meaning of session

Sources: [current scope](current-scope.md), historical [Engineering Plan](Netra_Final_Engineering_Plan.md) §9 and Appendix A, [CLAUDE.md](../../CLAUDE.md), [domain rules](../../.claude/rules/), [contracts](../../shared/contracts/) and [runtime baseline](runtime-baseline.md).

The System Lead approved this clarification on 12 September 2026:

- **Identity service** owns account/user identity, credential verification, device access and the security binding between an account and a session. It establishes whether a principal may access that session, including its identity/access/lifecycle aspect.
- **Session service** owns canonical mutable active-session state and all versioned session mutations: reading/navigation position, interaction state, pending session context and session version.

The phrase “users, sessions and device access” in Engineering Plan §9.1 refers to Identity's security/lifecycle responsibility. It does not transfer mutable learning/navigation session state to Identity. Appendix A, CLAUDE.md and coordinator.md govern mutable session-state ownership. Both categories persist authoritatively in PostgreSQL. This clarification adds no new service or ownership boundary.

## Ownership table (approved target; persistence integration remains pending)

Service ownership identifies the validator/writer for a record category, not exclusive access to an entire database. M2 owns persistence infrastructure; domain owners retain their write policies.

| Data category | Authoritative owner and storage | Derived copies / boundary |
|---|---|---|
| Accounts, verified identity, device access, account–session security binding | Identity service; PostgreSQL | Verified connection context is temporary; client IDs do not grant access. |
| Mutable sessions and pending session context | Session service; PostgreSQL | Client state and graph checkpoints cannot independently replace canonical session truth. |
| Reading/navigation position and preferences | Session service; PostgreSQL | Client cache and optional Redis are copies. Actual playback acknowledgement controls saved progress; downloaded audio is not heard content. |
| Source identity, immutable source versions, reading blocks and mapped search chunks | Ingestion service; PostgreSQL | Pinecone contains search projections; S3 stores original bytes. Existing sessions remain pinned to their version. |
| Source-linked visual structures and video evidence | Content/ingestion persistence in PostgreSQL; M3 owns multimedia production/validation | Preserve source/version, locations and observed vs generated/uncertain content. Provider assets do not replace canonical provenance. |
| Questions, private rubrics and assessment attempts | Learning service; PostgreSQL | Attempts are authoritative history. Question versions remain traceable; public delivery excludes private answer fields. |
| Delivered study activity, stated reasoning, feedback and assistance | Learning service; PostgreSQL target | Complete representation/commit path remains an integration gap. Preserve factual records, not automatic learning labels. |
| Legacy learning-status/review artifacts | Existing code/schema retained; outside current requirements | Removal of product requirements does not authorize deleting records, databases or projections. |
| Concepts and prerequisites | Content curation service; PostgreSQL | Neo4j relationships are projections. Tutor does not silently create concepts or prerequisite policy. |
| Session summaries and covered topics | Session service; PostgreSQL | Summaries reference events; exposure/coverage is not assessment or mastery. |
| Tutor lesson execution state | Tutor through application-managed LangGraph checkpointing in PostgreSQL | Private lesson continuity; Session service owns active-lesson/return context and Learning service owns committed assessment evidence. |
| Jobs and outbox | Job service/runtime; PostgreSQL | No independent queue truth. Outbox creation shares the canonical mutation transaction. |
| Audio metadata, cache identity and reservations | Speech service; PostgreSQL | S3 audio and bounded local completed-audio cache; quota reservations are not refunded merely because playback stops. |
| Raw source bytes | Private S3 (boto3==1.43.92, approved 2026-09-12) | PostgreSQL owns access and object/version mapping. Provider copies and local caches are not source authority. |
| Last stable result set | Session service; PostgreSQL (approved 2026-09-12) | `SessionState.last_result_set` holds only a `{result_set_id, created_at}` reference; the approved design places the ordered evidence-id list in a Session-service-owned table (concrete repository/migration pending), resolved through the same PostgreSQL-backed evidence authorization as any other reference. |
| Semantic vectors | Pinecone derived projection | Rebuild from canonical content and compatible embedding configuration; search-only authority. |
| Graph projection | Neo4j derived projection; M4 owns projection behavior | Rebuild from PostgreSQL concepts, prerequisites, assessments and covered-topic records. |

## Session state and concurrency

Canonical session concepts include account context, active source/document version, current reading block and sentence, last acknowledged playback position, interaction mode, connection state, active Tutor lesson, pending question/context, stable last result set and a monotonically increasing session version. These are semantic concepts, not an approval of new wire fields.

Interaction/learning state and connection state remain separate. Canonical interaction-mode vocabulary (approved 2026-09-12): `idle`, `reading`, `tutor_lesson`, `quiz`. "Listening"/"answering"/"waiting_for_answer" were rejected as separate values — the first is client-local playback state, not learning-flow state; the second and third are derivable from a non-null pending question within `tutor_lesson`/`quiz` rather than a value that could drift out of sync with it. "Navigation" was also rejected: deterministic commands complete synchronously, and a navigation command's "leaves the lesson while preserving the outstanding question" behavior is already satisfied by the pending question surviving independently of mode. ConnectionState (`connected`, `reconnecting`, `disconnected`) remains a separate, unchanged vocabulary and never appears in `session.snapshot`. `session.snapshot` is now a typed, reference-only sub-schema of `server_to_client.schema.json` — see [message flow](message-flow.md) for its field table and the last-stable-result-set design.

Session mutations use expected-version checks and committed replay identity/results. Duplicate operations return their prior result rather than repeat navigation. A source update does not silently change a pinned session. Reconnection cannot restore cancelled output.

## Canonical history and derived state

Only Learning service validates and commits Tutor proposals. Retain delivered activity, each answer, student-stated reasoning, feedback and assistance as factual history. “Studied — understanding not tested” is an activity description, not a status enum. Automatic labels and review schedules are removed requirements; existing `StatusDerivationPolicy`, `ReviewIntervalPolicy` and legacy contract labels remain compatibility artifacts, not policies to complete. Optional-check grounding criteria and complete factual-history representation still require work. History remains authoritative regardless of projection availability. Exact facts remain outside compacted dialogue summaries.

Pinecone and Neo4j are derived and rebuildable. Projections cannot overwrite PostgreSQL truth. Apply projection updates idempotently with source/event-version ordering so older retries cannot overwrite newer results. Resolve vector references against PostgreSQL for access, deletion and source-version compatibility before using them as evidence. A pinned older source version is not automatically invalid merely because a newer one exists.

Jobs execute at least once through PostgreSQL leases, retry/backoff and outbox. External calls occur outside long database transactions. Checkpoint writes and business writes must not be assumed atomic across separate connections. Agents never become data authorities and never receive raw database access.

Redis is optional and not a correctness dependency. Retention periods and other unapproved policy values remain **Pending approved contract/policy decision.** The `error` schema is no longer empty (approved 2026-09-12; see [message flow](message-flow.md)); the job schema remains empty and its necessity/shape is still **Pending approved contract/policy decision.** Empty schemas do not authorize invented definitions beyond what is now committed. See [message flow](message-flow.md) for the last-stable-result-set storage design and the remaining unresolved job-contract decision.
