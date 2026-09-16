---
paths:
  - "api/**/content/**"
  - "api/**/ingestion/**"
  - "api/**/retrieval/**"
  - "api/**/db/**"
  - "api/**/repositories/**"
  - "**/migrations/**"
  - "worker/**"
---

# Backend and data rules

Owner: M2 Backend/Data.
Read [current scope](../../docs/architecture/current-scope.md), [ownership](../../docs/team/ownership.md)
and the corresponding M1–M5 guide before implementation. These rules share the
canonical authorities used by root AGENTS.md and CLAUDE.md.
Apply CLAUDE.md and task-relevant domain rules for individual worker jobs.
Worker-wide matching covers shared persistence/job mechanics, not ownership
of multimedia algorithms or Learning service policy.

## Authority and ownership

PostgreSQL is authoritative for structured records, access and source identity.
S3 stores durable source bytes; PostgreSQL links objects to immutable versions.
Pinecone is a rebuildable semantic-search projection.
Neo4j is M4's rebuildable learning/concept projection.
Do not implement Tutor behaviour or invent learning labels in data infrastructure.
Owning database infrastructure does not mean owning every domain write policy.

Expose typed repository/service interfaces, never database handles to agents.
Use application-supplied authenticated context for account-scoped operations.
Every service boundary must enforce access; opaque IDs are not authorization.
Use parameterized developer-authored queries or ORM expressions.
Never accept SQL, query fragments or arbitrary filters produced by a model.

## Database and migrations

Follow the approved SQLAlchemy, Alembic and driver baseline.
Use SQLAlchemy asyncpg connections for application async database access.
Use the prescribed Psycopg connection/pool for LangGraph PostgreSQL checkpoints.
Do not treat these pools, connection strings or transaction lifecycles as identical.
Keep checkpoint setup separate from application migration ownership.
Do not apply checkpoint autocommit requirements to business transactions.

Encode business invariants with transactions and appropriate constraints.
Use version comparisons and operation IDs where writes may conflict or repeat.
Add reviewed migrations for schema changes; preserve applied migration history.
Changes to another domain's schema require that owner's coordinated review.
Never assume separate checkpoint and business writes are atomically committed.

## Ingestion and source versions

Implement ingestion as a deterministic, recoverable workflow.
Preserve the original authorized source and its object/version identity.
Record content hashes, parser configuration/version and stage results.
Keep navigation reading blocks distinct from retrieval search chunks.
Preserve mappings between chunks, reading blocks, pages and source versions.
Never fabricate a page, heading, locator or successful extraction result.

Activate a new source version only after the preserved validation/indexing gates.
Retain prior versions needed by pinned sessions.
Never silently migrate an active reading session to a newly indexed version.
Deletion/access revocation must prevent new authorized delivery of removed content.
Apply the existing retention/deletion policy; do not invent retention periods.

## Retrieval and projections

Use authorized source scope before search and canonical checks after search.
Resolve Pinecone matches against PostgreSQL before constructing model context.
Reject inaccessible, deleted, stale or incompatible-version references.
Return canonical evidence references and bounded authoritative excerpts.
Similarity is a candidate-ranking signal, not proof of factual support.

Pin embedding model/version, dimensions and index configuration.
Do not mix incompatible document/query embeddings or reuse an incompatible index.
Projection writes must be idempotent and version-aware.
Older retries must not overwrite newer projected state.
Pinecone failure must not erase canonical source records.
Use the plan's authorized PostgreSQL text-search reduced mode where supported.
Distinguish empty results, unavailable dependencies and denied access.

## Durable jobs and outbox

Use leased PostgreSQL jobs with at-least-once execution.
Claim work in a short transaction, commit, then perform external work.
Use lease expiry/renewal, bounded attempts, next-run time and operation identity.
A worker that loses its lease must not commit as the current owner.
Record completed stages and remote operation IDs for safe recovery.
Do not blindly repeat an external call after an uncertain completion.
Reconcile/reuse its result when supported; otherwise expose the uncertainty.

Use exponential backoff with jitter and explicit permanent-failure handling.
Do not retry denied access, invalid input or exhausted quota indiscriminately.
Write outbox events in the transaction containing their canonical mutation.
Acknowledge delivery only after the corresponding effect is safely recorded.
Idempotency must cover crashes between an effect and its acknowledgement.
Never claim universal exactly-once execution across provider APIs.

## Verification focus

Use available isolated fixtures/checks for migrations, transaction rollback,
duplicate job delivery, lease loss, worker crashes and outbox replay.
Check source pinning, unauthorized vector IDs and stale projection events.
Do not run migrations against an unspecified/shared database.
Network, dependency installation and destructive cleanup require explicit scope.

## Current source scope

Target PDF and uploaded lectures plus selected, supported YouTube sources. Drive,
general web ingestion and additional formats are deferred. M3 owns visual/table
extraction semantics; M2 owns storage, source versions and reading-block mappings.
Removing automatic learning labels/review does not authorize dropping databases,
projections, schemas or existing code. Factual history needs M4/M2 schema review.
