# Integration status

Living record for the M1–M5 integration branch. Update it at every slice so work
can continue after a context reset without repeating investigations. Evidence
classes are kept separate: **fixture** (doubles), **real local** (real processes,
databases or sockets on this machine), **Windows/NVDA** (assistive-technology
sessions with a person) and **live** (provider/AX/Modal). Nothing here claims
production readiness.

## Checkpoint

- **Done:** slice A (runtime + baseline), slice B (database, migrations,
  transactions, learning persistence, worker projection/cancellation), slice C
  (real app composition, session routes, model adapters behind controlled
  transports, LangGraph execution, ElevenLabs framing), slice D (WPF client
  live mode against the real server).
- **Remaining in C:** M3 media service + worker multimedia composition,
  twelvelabs/tavily SDK verification, lecture journey, LlamaParse provider.
- **Next slice:** E — OpenTelemetry pins and a real OTLP exporter behind
  `platform/tracing.py`'s `SpanExporter`; stalled/failing collector tests;
  overhead on the real server path.

### How to run the client against the real local server (slice D)

1. `source D:/Agentathon/netra-integration-envs/env.sh`
2. `PYTHONPATH="api/src;worker/src" $PY api/tests/server/serve_for_client.py --database-url "$NETRA_TEST_DATABASE_URL" --info-file <scratch>\live-server.json`
   (seeds a test account with two ready sources and one not ready, another
   account's source, and a token that expires in 1 s, all on the disposable DB;
   writes the endpoint and **test** tokens to the local info file; delete the
   file afterwards). Paced stand-in synthesizer, audio cache off by default.
3. Tests: `NETRA_LIVE_SERVER_INFO=<scratch>\live-server.json dotnet test client/Netra.sln --no-restore`.
4. App (manual, not yet run by a person): `cmdkey /generic:Netra:api /user:netra /pass:<token>`,
   `set NETRA_API_ENDPOINT=ws://127.0.0.1:<port>/v1/ws`, start `Netra.Desktop`.
   Remove with `cmdkey /delete:Netra:api`. If the server is not up yet, start it
   and choose Refresh in the library (no restart needed). This is an operator
   integration aid; credential issuance (PKCE) and the production credential
   store are still undecided (D-CRED).

### Live readiness check: AX tracing and Prometheus-2 on Modal (2026-09-19)

**Not ready: nothing can authenticate yet.** Names only; no value was read or printed.

- Not set in process, Windows User or Windows Machine scope:
  - `NETRA_EVAL_JUDGE_URL`, `NETRA_EVAL_MODAL_PROXY_TOKEN_ID`, `NETRA_EVAL_MODAL_PROXY_TOKEN_SECRET`
  - `MODAL_TOKEN_ID`, `MODAL_TOKEN_SECRET`
  - AX: `ARIZE_*`, `NETRA_AX_*`, `NETRA_TRACING_MODE`
  - `NETRA_GEMINI_API_KEY`, `NETRA_GROQ_API_KEY`
- No `~/.modal.toml`, repo `.env` or `secrets/`.
- No Modal CLI, `modal`, `arize` or OpenTelemetry package in any local Python or in `uv.lock`.
- AX credential names are not defined in code yet. `tracing_mode=ax` has no exporter (slice E). Proposed names: `NETRA_AX_SPACE_ID`, `NETRA_AX_API_KEY`, `NETRA_AX_PROJECT_NAME`.
- Reachable over HTTPS without credentials: api.modal.com, otlp.arize.com, app.arize.com, huggingface.co, pypi.org.
- Judge model `prometheus-eval/prometheus-7b-v2.0` at revision `66ffb1f…`: public, ungated, Apache-2.0, 8 safetensors shards. No Hugging Face token is needed.
- **Blocks deployment:** `evaluation/deploy/prometheus_modal.py` refuses to import while 7 pins are `PENDING_M2_REVIEW`.
- **Blocks meaningful scoring:**
  - 0 human labels; the floor is ≥10 per criterion.
  - No held-out split and no human error analysis.
  - Draft references are suggestions only.
  - No real Netra candidate outputs, which need the live Gemini and Groq keys.
- **Fix before any GPU use:** `RunAllowance` charges the endpoint's `gpu_seconds`. That excludes cold start, weight download and loading, and the 60 s scale-down idle, so the soft budget undercounts. Authenticated `/result` and reconcile requests also wake the A100, because the web app lives on the GPU class.
- Modal credits, workspace budget and spend limit are not accessible without Modal authentication.

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
| `108e0b5` | docs | Slice A/B evidence |
| `9359275` | C | Real M2/M4 composition, session/source routes, operator token provisioning CLI, stale-question reconciliation, real server journey |
| `61d1aa6` | C | Gemini/Groq adapters (pinned SDKs, controlled transports); retrieval reduced mode |
| `e7ac1a2` | C | Composed turns run through the pinned LangGraph graph; Postgres checkpoint resume |
| `0f226b7` | C | Durable answer journey over the real server |
| `5d3e55b` | C | ElevenLabs adapter (not registered in production: D-QUOTA) |
| `f8c2e8a` | D | Client live mode: typed API client, Credential Manager source, server source catalog, live tests; library keyboard fix |
| `37c0f9a` | D | Projection test isolation; slice C/D record |
| (next) | D redo | Re-review of slice D: retryable start (`LiveSession`), typed credential rejection, total failure wording, request timeout, re-entrancy, selection keep, path prefix; live tests in the app's real order; mutation-checked |

## Test counts (latest)

| Command | Collected | Passed | Failed | Skipped | Deselected |
|---|---|---|---|---|---|
| `$PY -m pytest -q -p no:cacheprovider` at `4ede791` (before fixes) | 1056 | 1035 | 2 | 0 | 19 |
| same at `850e334` | 1093 | 1050 | 0 | 1 | 42 |
| `NETRA_TEST_DATABASE_URL=… $PY -m pytest -q -p no:cacheprovider -m integration` at `4ede791` | 19 | 18 | 1 | 0 | 1037 |
| same at `850e334` | 42 | 41 | 0 | 1 | 1051 |
| default run, slice D | 1121 | 1073 | 0 | 1 | 47 |
| integration run, slice D (twice) | 47 | 46 | 0 | 1 | 1074 |
| `dotnet test client/Netra.sln --no-restore` at `4ede791` | 88 | 88 | 0 | 0 | — |
| same, slice D first pass, no live server | 114 | 112 | 0 | 2 (live, visibly skipped) | — |
| same, slice D first pass, `NETRA_LIVE_SERVER_INFO` set | 114 | 114 | 0 | 0 | — |
| same, slice D redo, no live server | 135 | 131 | 0 | 4 (3 live + 1 opt-in credential read) | — |
| same, slice D redo, `NETRA_LIVE_SERVER_INFO` set (3 consecutive live runs identical: 18 frames) | 135 | 134 | 0 | 1 (opt-in credential read) | — |
| Python default / integration after the redo | 1121 / 47 | 1073 / 46 | 0 / 0 | 1 / 1 | 47 / 1074 |

Slice D redo mutation check (each fix undone in turn, its test must fail, then
restored byte-for-byte): 8/8 detected — rejected credential retried, no Open
re-entrancy guard, selection lost on Refresh, Refresh not (re)starting the
session, timed-out pin not retried, non-total failure text, resync reported
before a snapshot arrived, path prefix dropped. (The re-entrancy test first
hung under its mutation, by its own design; it was rewritten to fail fast and
re-checked.)

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
| Evidence identity M2 → M1 → M4 | working (real local) | INT-03 tests; Coordinator → Tutor journey over real retrieval + async resolver |
| FastAPI app boot, real WebSocket, auth at routes and upgrade | working (real local) | `api/tests/server/` (4, uvicorn over TCP) |
| Session creation, source listing, explicit pin, replay/conflict | working (real local) | Python server journey + C# `LiveServerTests` |
| Coordinator → Tutor with Gemini/Groq adapters | working (real local, controlled transports) | no live provider call made |
| LangGraph execution + Postgres checkpoint resume | working (real local) | graph state is not checkpointed across restarts (runtime objects in state) |
| Speech framing (ElevenLabs adapter) | unverified live; framing real local | not registered in production (D-QUOTA) |
| WPF client → real server, in the app's order (`LiveSession` start, HTTP open while the socket is connected) | working (real local, paced stand-in synthesizer, ScriptedPlayer) | `LiveServerTests` (3): 3 own sources listed (1 not ready, not openable), other account's hidden; open A; 8 frames admitted in sequence and assembled; started/completed acks persisted; STOP mid-stream (0 frames after); switch to B starts at B's first sentence; drop mid-stream fences that generation across reconnect; version conflict → resync → Open succeeds; expired token → 401 on HTTP, typed rejection on the upgrade, reconnect not retried |
| Credential Manager read | partly verified | decode + absent-target read (real `CredReadW`, read-only); reading a stored credential needs a tester-created throw-away entry (opt-in test); **no test writes to the user's vault** |
| Library view keyboard paths | working (real WPF binding, off-screen window) | `LibraryViewBindingTests` (3); **NVDA announcement not verified** (needs a person) |
| Audible playback, NVDA, real App startup in live mode | blocked (needs Windows/NVDA session with a person) | not claimed |
| Voice input | disabled | not claimed; INT-11a mic protocol unapproved |
| Upload / YouTube discovery in client | fixture | upload/job contract empty; no discovery route |
| Optional-check support (D2) | blocked (decision P-1) | binding enforced; support fails closed |
| Factual activity/assistance/reasoning records (D3) | blocked (review) | proposal code only; no table |
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
| C-1 | Every FastAPI route returned 422 | M1 `main.py` | Real uvicorn: framework types imported inside `create_app` under postponed annotations | Module-level imports; lifespan replaces deprecated `on_event` |
| C-2 | Answered question could be restored after a crash between learning commit and session update (was C-open-1) | M4 store ↔ M1 session | Real server test | Compare-and-clear on resume and return-to-question |
| C-3 | Differently spelled UUID counted as a new pin (was C-open-2) | M1 route ↔ M2 ids | — | Canonical UUID in route and fingerprint |
| C-4 | Pinecone-path configuration failure escaped as an error instead of reduced mode | M2 retrieval → M1 tools | `EmbeddingConfigurationError` in real journey | Semantic failures classified unavailable → full-text only |
| C-5 | Production composition registered no M2/M4 services | bootstrap | Real server | `production_dependencies` with one `AsyncSession` per call (`SessionScoped`) |
| D-1 | Client could never connect: no endpoint, no credential source, no session creation | M5 ↔ M1 routes | `App.xaml.cs` built the socket with `credentials: null` | Explicit live mode (`NETRA_API_ENDPOINT` + Credential Manager), HTTP session creation, resume over `/v1/ws`; fixture mode says so |
| D-2 | Keyboard/Enter and the "Select this result" button passed `null` for lecture results, so a result could never be selected | M5 XAML | `LibraryViewBindingTests` fails on the old binding (verified by reverting) | Parameter bound to the list's selected item |
| D-3 | `LibraryViewModel` mutated bound collections after `ConfigureAwait(false)`; hidden by synchronous fixtures, breaks with a real network call | M5 | Code audit (WPF cross-thread rule) | Continuations stay on the UI thread |
| D-4 | Reconnect leaked the previous socket and receive-loop token source | M5 `NetraWebSocketClient` | Code audit | Released on reconnect |
| D-5 | Sends from independent paths were not serialized; WebSocket's documented contract is one outstanding send | M5 | **Not reproduced**: the managed `ClientWebSocket` serializes internally (test passes with or without the lock) | Send lock kept for contract conformance only |
| D-6 | Projection pipeline test consumed other tests' outbox events in the shared DB (was C-open-5), and failed in the full integration run | test isolation | Full run failed; the test alone passed | Graph double scoped to the test's own attempts |
| D-7 | A start that failed (server down at launch, socket refused) was never retried; Refresh before the session existed listed with an empty session id | slice D first pass (`App`) | Code review; `ReconnectCoordinator` only reacts to drops of an established connection | `LiveSession.EnsureStartedAsync`: create once, connect if disconnected, shared in-flight attempt; Refresh retries it |
| D-8 | Startup failure said "Reading cached material still works" | slice D first pass | `StudyPacketCache` is not wired anywhere | Removed; status says how to retry |
| D-9 | Failures outside a fixed set (e.g. `ProtocolException`) left "Opening …" on screen | slice D first pass | Code review; mutation check | `FailureText.Describe` is total |
| D-10 | HTTP used the 100 s default timeout; a timed-out pin was not retried | slice D first pass | Code review | 15 s client-local timeout; one retry under the same request id (server replays) |
| D-11 | A second Open while one was in flight sent a second pin and came back as a misleading conflict | slice D first pass | Code review | Ignored with an accessible status |
| D-12 | Refresh dropped the student's list selection (bound `ListBox` pushes null on `Clear`) | slice D first pass | Code review | Restored by source id |
| D-13 | An expired/revoked token (403 on the upgrade) was retried as a network failure: 8 attempts, then "try again later" | M5 `NetraWebSocketClient`/`ReconnectCoordinator` (pre-existing) | **Reproduced live before the fix**: generic `WebSocketException` 403, 8 attempts | `CredentialRejectedException` from the upgrade status; not retried; named cause |
| D-14 | HTTP base dropped any path prefix in front of `/v1/ws` | slice D first pass | Code review | Prefix kept |

Slice D first-pass evidence gaps corrected in the redo: the live test pinned
before connecting (the app pins after), used one source (no switch), dropped
the socket while nothing was streaming (so "old audio never resumes" was
vacuous), and relied on a server whose audio cache made later runs deliver one
frame per segment. The redo tests the app's order, a switch, a mid-stream drop
with a guard that fails if nothing was streaming, and disables the launcher's
cache so every run is identical.

## Open items found (to fix in later slices)

| ID | Item | Plan |
|---|---|---|
| C-open-3 | Bounded-failure wording for a refused quiz draft says "a required service is unavailable" | Review with M5 wording; not changed yet |
| C-open-4 | PyMuPDF provider imports the deprecated `fitz` alias | Use `import pymupdf` (same pinned package) |
| D-open-1 | `NetraWebSocketClient.CloseAsync` cancels its receive loop first, which aborts the socket, so no close handshake is sent. No production caller (shutdown disposes the socket); tests only | Close output first, then stop the loop; avoid a double `Disconnected` (M5) |
| D-open-5 | `ConversationViewModel` reports every snapshot (including navigation replies) as "Session restored" | M5 wording review |
| D-open-2 | Each app launch creates a new session; resuming the previous session after restart needs a persisted session id | M1/M5 decision |
| D-open-3 | `NetraHttpClient` scaffold is unused (no auth, no typed errors); `NetraApiClient` supersedes it for the session routes | Remove or merge after M5 review |
| D-open-4 | The HTTP session/source route shapes are an integration proposal | Formal M1/M5 sign-off |

## Decisions needed

| ID | Decision | Recommendation | Blocks |
|---|---|---|---|
| D-LIC | PyMuPDF 1.28.2 is AGPL-3.0 (or commercial) | Project owner decides; this integration accepts no obligation. Alternative: LlamaParse + Tesseract only. | Distribution, not local integration |
| D-CONCEPT | No canonical concept records exist in PostgreSQL, so every attempt projection dead-letters (`CONCEPT_NOT_PROJECTED`) | Add a reviewed concept catalog (M4 semantics, M2 storage) populated from curated source metadata and projected first; do not MERGE concepts from attempt data | Neo4j projection only; attempts stay canonical |
| D-NEO4J | Real Cypher never executed | Authorize pulling `neo4j:5.26.30` for a disposable local container (same controls as PostgreSQL) | Neo4j verification only |
| D-QUOTA | Speech quota amount and a durable quota ledger | Approve an amount; store the ledger in PostgreSQL | Registering ElevenLabs in production |
| D-CRED | Desktop credential issuance (PKCE sign-in) **and** the production credential store (INT-10a: DPAPI `ProtectedData` or Credential Manager) | Credential Manager is used only as an integration aid (operator `cmdkey`); M1/M5 decide both | Real student sign-in |
| D-BUDGET | The turn budget is in memory, so a retransmission after a restart gets a fresh budget | Persist budget use per request id | Budget across restarts |
| D-MIC | Mic protocol (INT-11a) | Approve before building the Deepgram adapter | Voice input |
| D3 / P-1 / P-2 / P-3 | Factual activity schema; optional-check support definition; declined-check record; evidence change while a question is pending | See M4 handoff recommendations | Optional checks, history records |

## Independent work that can proceed

Slices C–E need no live provider or new product policy for their local parts.
