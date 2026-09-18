# Integration status

Living record for the M1–M5 integration branch. Update it at every slice so work
can continue after a context reset without repeating investigations. Evidence
classes are kept separate: **fixture** (doubles), **real local** (real processes,
databases or sockets on this machine), **Windows/NVDA** (assistive-technology
sessions with a person) and **live** (provider/AX/Modal). Nothing here claims
production readiness.

## Checkpoint

- **Done:** slice A (runtime + baseline), slice B (database, migrations,
  transactions, learning persistence, worker projection/cancellation).
- **Current slice:** C — API/worker/service composition: real FastAPI app,
  real WebSocket, M2/M3/M4 services through `bootstrap.compose`, LangGraph.
- **Next action:** read `bootstrap.py`/`main.py`, register real M2
  repositories + `PostgresLearningStore` + `TutorRunner` in production
  composition, boot uvicorn against the disposable database and drive
  `/v1/ws` with a real WebSocket client.
- **Known crash boundary to close in C:** a learning commit and the session's
  `pending_question` reference live in different transactions; reconnect must
  reconcile an already-answered question (see C-open-1).

## Identity and environment

| Item | Value |
|---|---|
| Branch / worktree | `codex/netra-integration` at `D:\Agentathon\netra-integration` (no upstream; unset so a bare push cannot target `main`) |
| Starting SHA | `origin/main` `4ede791b102eea30172c535286726c1b4ee4836e` (Merge PR #14, M2) |
| Verified ancestors | M1 `162e50b`, M2 `79a7078`, M3 `d45cb54`, M4 `16d1859`, M5 `903512a`, docs `f967f43` — all ancestors of `4ede791` (`git merge-base --is-ancestor`). The handoffs' "unpublished" statements are superseded by Git. |
| Other worktrees (untouched) | `D:\Agentathon\netra` (`codex/documentation-baseline`), `netra-m1`, `netra-m5` |
| OS | Windows 11 Home 10.0.26200 |
| Python | CPython 3.13.15 (system install, used read-only as the base interpreter) |
| App environment | `D:\Agentathon\netra-integration-envs\app-venv`, from `uv sync --locked` (uv 0.12.13 in its own tool venv, isolated `UV_CACHE_DIR`, `UV_PYTHON_DOWNLOADS=never`). Installed set = lock minus platform-conditional `uvloop`, `httpx2-jsfetch` and the non-package root project. No extra packages. |
| Env helper | `source D:/Agentathon/netra-integration-envs/env.sh` (Git Bash) sets `UV`, `UV_CACHE_DIR`, `UV_PROJECT_ENVIRONMENT`, `PY`, `NETRA_TEST_PG`, `PGC` |
| Lock | `uv.lock` revision 3, 106 packages, `exclude-newer 2026-09-12T00:00:00Z`, prereleases disallowed; `uv lock --check` exit 0; unchanged |
| .NET | SDK 10.0.401 (matches `client/global.json`) |
| Docker | Engine 29.5.3 / Compose v5.1.4 locally (deployment baseline 29.7.2 / 5.3.1 is not what runs here) |
| Test PostgreSQL | `postgres:17.11` digest `sha256:67f41722b7a8cbdb868a44a4995c846eddfdc2973bccb291ce937dce88ad5675`, Compose project `netra-integration-test`, tmpfs, `127.0.0.1:55432`. Databases `netra_test` (integration tests) and `netra_migrate` (reversal round-trips), both created by this task. |
| Push | `.claude/settings.json` denies `git push`; the user publishes the branch |

## Commits on this branch

| SHA | Slice | Summary |
|---|---|---|
| `4badfbf` | A | Stale M4 grounding fixture; local-only golden PDF skip; this document |
| `336bd15` | B | Transaction ownership helper; replay-before-version on PostgreSQL; index drift |
| `4d6cd5c` | B/C | INT-03 evidence identity: async resolver, `evidence_version` carried and compared, canonical UUID source version in contract |
| `0cbcc33` | B | INT-08 migration 0008 + `PostgresLearningStore` + atomic answer commit |
| `850e334` | B | Neo4j writer, projection pool, cancellation classification, lease tracking |

## Test counts (latest)

| Command | Collected | Passed | Failed | Skipped | Deselected |
|---|---|---|---|---|---|
| `$PY -m pytest -q -p no:cacheprovider` at `4ede791` (before fixes) | 1056 | 1035 | 2 | 0 | 19 |
| same at `850e334` | 1093 | 1050 | 0 | 1 | 42 |
| `NETRA_TEST_DATABASE_URL=… $PY -m pytest -q -p no:cacheprovider -m integration` at `4ede791` | 19 | 18 | 1 | 0 | 1037 |
| same at `850e334` | 42 | 41 | 0 | 1 | 1051 |

The default configuration deselects `integration` tests (`addopts = -m 'not integration'`):
a green default run is **not** database acceptance. The single skip in each run
is the E3/E4 golden check that needs gitignored, non-redistributable source
documents (absent on this machine).

Migration evidence (real PostgreSQL 17.11): `alembic upgrade head` 0001→0008 on
`netra_test` and `netra_migrate`; `downgrade base` leaves only `alembic_version`
and no enum types; step-downs 0006/0005/0004 and 0008→0007 re-applied cleanly;
`test_migration_drift` asserts zero ORM/Core-vs-database differences at head.

## Capability map

Status: working (real local evidence) · unwired (implemented, not composed) ·
incomplete (partial) · unverified (fixture only) · blocked (decision/hardware/live).

| Capability | Status | Evidence / gap |
|---|---|---|
| Locked 3.13.15 environment | working | clean `uv sync --locked`; suites green |
| Migrations 0001→0008, reversal | working (real local) | see migration evidence |
| M2 source/reading/chunk/job/outbox repositories | working (real local) | 19 original integration tests + ownership tests |
| M1 session repository concurrency/replay | working (real local) | `test_postgres_session_service.py` (5) |
| Pending questions, attempts, atomic commit + outbox | working (real local) | `test_postgres_learning_store.py` (8) |
| Outbox → job → projection handler | working (real local, Neo4j double) | `test_learning_projection_pipeline.py` (4) |
| Neo4j writer | unverified against a server | driver-signature contract tests only (D-NEO4J) |
| Evidence identity M2 → M1 → M4 | working (fixture + real resolver type) | INT-03 tests; real-resolver journey pending in C |
| FastAPI app boot, real WebSocket | unverified | slice C |
| LangGraph integration | fixture | `test_langgraph_wiring_when_pinned_package_is_installed` now runs (package installed) |
| Optional-check support (D2) | blocked (decision P-1) | binding enforced; support fails closed |
| Factual activity/assistance/reasoning records (D3) | blocked (review) | proposal code only; no table |
| Real client connection | blocked on routes/credential issuance (INT-10) | slice D |
| AX exporter | unwired (no OTel pins) | slice E |
| Prometheus/AX evaluation | blocked on live authorization | slice F |

## Defects fixed

| ID | Defect | Producer → consumer | Evidence | Fix / acceptance test |
|---|---|---|---|---|
| A-1 | `test_m4_pending_grounding_decision_is_a_bounded_failure_not_a_crash` failed | M4 (citation binding added later) → M1 test | Draft double cited nothing; both paths traced through the real transport | Fixture cites resolved evidence; separate test keeps uncited/unresolved citation as bounded `failed`. Implementation unchanged. |
| A-2 | Golden PDF check failed on every clean checkout | M2 fixture policy → evaluation suite | `FileNotFoundError` | Explicit skip only when the local-only documents are absent |
| B-1 | `_close_read_transaction` committed any pending write on a shared session; worker `enqueue` also rolled caller writes back | M2 repositories → any caller | Reproduced: two "foreign" uncommitted rows were published | `db/transactions.py::close_read_only_transaction` fails closed (`ForeignTransactionError`); `test_transaction_ownership.py` (5) |
| B-2 | Racing identical retransmission got `SESSION_VERSION_CONFLICT` on PostgreSQL | M1 repo vs in-memory contract | Reproduced with a two-transaction barrier | Claim request identity before the version UPDATE; stable over 5 runs |
| B-3 | Migration-only indexes absent from metadata (autogenerate would drop one-active uniqueness) | M2 | `compare_metadata` 3 diffs | Declared in ORM; drift test = 0 |
| B-4 | `EvidenceResolver` protocol sync, only real implementation async; M3 helpers, M4 Tutor called it synchronously | M2 → M1/M3/M4 | Code audit | Protocol async; consumers `maybe_await` |
| B-5 | M1 tools dropped M2 `evidence_version`; test wrapper stamped `evidence_version=1` on all tool output | M2 → M1 → M4 | Code audit | Tools pass it through; wrapper removed; `test_real_adapters_carry_the_resolvers_evidence_version` |
| B-6 | Tutor compared only source version | M1/M2 → M4 | — | Compares `evidence_version`; absent version refused |
| B-7 | Contract examples used non-UUID source versions no producer emits | contracts | — | `format: uuid` + comments; Python mirror normalizes/refuses; examples fixed; C# treats as opaque (no change) |
| B-8 | No durable learning store; attempt and question closure non-atomic; no outbox event; concurrent answers could both commit | M4/M2 | Code audit | Migration 0008, `PostgresLearningStore.commit_answer`; 4-way race commits one |
| B-9 | `LearningService` required legacy mastery thresholds to be constructed | M4 → composition | — | Optional; `derive_current_status` refuses without it |
| B-10 | Projection job type never registered; no Neo4j writer | M4/M2 → worker | `build_pools` audit | Writer + pool; disabled visibly without `NETRA_NEO4J_*` |
| B-11 | Explicit cancellation retried as failure | M3 → M2 dispatcher | D-CANCEL | `JobCancelled` → terminal `cancelled` |
| B-12 | Lease-based cancellation stopped renewed jobs at original expiry | M3 recorder ↔ dispatcher heartbeat | Code audit | `LeaseHolder.tracking(job)`; lease expiry is `LeaseLostError` |
| B-13 | `alembic.ini` split `prepend_sys_path` on `:` (breaks Windows drive paths) | M2 tooling | Deprecation warning | `path_separator = os` |

## Open items found (to fix in later slices)

| ID | Item | Plan |
|---|---|---|
| C-open-1 | Learning commit and session `pending_question` are separate transactions; after a crash between them reconnect could restore an answered question | Reconcile on resume/return-to-question against `PostgresLearningStore.is_answered` (slice C) |
| C-open-2 | `pin_source` compares raw strings, so a differently spelled UUID counts as a switch | Canonicalize at the route boundary (slice C) |
| C-open-3 | Bounded-failure wording for a refused quiz draft says "a required service is unavailable" | Review with M5 wording; not changed yet |
| C-open-4 | PyMuPDF provider imports the deprecated `fitz` alias | Use `import pymupdf` (same pinned package) |
| C-open-5 | Outbox consumer tests claim any unprocessed event in the shared disposable DB | Acceptable for disposable DB; note for test isolation |

## Decisions needed

| ID | Decision | Recommendation | Blocks |
|---|---|---|---|
| D-LIC | PyMuPDF 1.28.2 is AGPL-3.0 (or commercial) | Project owner decides; this integration accepts no obligation. Alternative: LlamaParse + Tesseract only. | Distribution, not local integration |
| D-CONCEPT | No canonical concept records exist in PostgreSQL, so every attempt projection dead-letters (`CONCEPT_NOT_PROJECTED`) | Add a reviewed concept catalog (M4 semantics, M2 storage) populated from curated source metadata and projected first; do not MERGE concepts from attempt data | Neo4j projection only; attempts stay canonical |
| D-NEO4J | Real Cypher never executed | Authorize pulling `neo4j:5.26.30` for a disposable local container (same controls as PostgreSQL) | Neo4j verification only |
| D3 / P-1 / P-2 / P-3 | Factual activity schema; optional-check support definition; declined-check record; evidence change while a question is pending | See M4 handoff recommendations | Optional checks, history records |

## Independent work that can proceed

Slices C–E need no live provider or new product policy for their local parts.
