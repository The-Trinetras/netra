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
- **Optimization and reliability phase (local evidence only):** 6 defects fixed
  with predeclared thresholds (OPT-1 to OPT-6), and 7 findings recorded for
  owners. The database-backed journey measurement is still pending because the
  PostgreSQL host was paused. See the section below and `docs/team/perf/`.
- **Remaining in C:** M3 media service + worker multimedia composition,
  twelvelabs/tavily SDK verification, lecture journey, LlamaParse provider.
- **Next slice:** E — OpenTelemetry pins and a real OTLP exporter behind
  `platform/tracing.py`'s `SpanExporter`; stalled/failing collector tests;
  overhead on the real server path.
- **Agent-a-thon layout (19–20 September):** the repository also follows the
  agentic-slice-kit layout (`slice/`, `demo/notes/`, `web/`, `scripts/`,
  `corpus/`, `.devcontainer/`), with one root `pytest.ini`/`conftest.py`.
  OpenRouter adapters implement the Coordinator and Tutor provider protocols
  (mock-transport tests only). See the README and the runtime baseline.
- **Step 0 decisions (20 September):** recorded in "Decisions made" below;
  what remains open is in "Decisions needed".
- **AWS (M2):** the RDS/PgBouncer/S3 Terraform is on `main` (PR #17) and those
  resources exist; see "AWS infrastructure (M2)".

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

### Evaluation dataset package (overnight, 2026-09-19)

Offline only: no Modal, GPU, provider or AX call. **Slice E (tracing exporter)
had not been started**; there was no slice E work to preserve.

- **Package:** `evaluation/datasets/netra_grounded_v1.json`, snapshot
  `netra-grounded-v1@2ad465bddd7e51a5`, 59 cases:
  - 21 development (exposed chapter-4 family);
  - 16 calibration candidates;
  - 22 held-out *candidates* (new unexposed miniatures, not frozen).
- **Review status:** 0 gold references, 58 suggested, 1 deliberately missing.
- **Where to start:**
  - report: `evaluation/review/netra_grounded_v1/report.md`
  - worksheet: `evaluation/review/netra_grounded_v1/worksheet.md`
  - template: `evaluation/review/netra_grounded_v1/review_template.json`
- **Checks:** `eval_cli.py check` → 0 errors, 1 warning (the same walkthrough
  question on two calibration renderings).
  - 117 excerpts verified verbatim.
  - 54 calculations recomputed exactly.
  - 9 withheld items verified against registry facts.
  - 141 deterministic assertions (77 critical).
  - Every reference passes its own assertions.
- **Tests:**
  - evaluation suite: 164 passed, 1 skipped (was 131);
  - default Python suite: 1106 passed, 1 skipped.
- **Minimum review batch to unlock meaningful scoring:** 10 calibration cases
  (listed in the report).
  - Then real Netra outputs (live keys), an authorized judge run, and human
    labels of those outputs.

Defects found and fixed in the evaluation workflow:

| ID | Defect | Fix |
|---|---|---|
| E-1 | Frozen outputs recorded no citations or structured facts, so citation/pending-question/grade checks were impossible | `FrozenOutput.cited_evidence_ids` / `structured`; absent ⇒ `not_evaluable`, never a pass |
| E-2 | `init-run` accepted an unfrozen held-out split | Refused unless `verify_frozen_heldout` passes |
| E-3 | No path to import real outputs; fixture text could be labelled as Netra output | `import-outputs`; origin set by the run's producer; units outside the run refused |
| E-4 | tutor-reference-v1 `ohm-dev-01`'s reference relied on R = V/I, which the case did not supply | Equation excerpt added in the new dataset; recorded in the case |
| E-5 | Two v1 references (and early drafts) were behaviour descriptions, not the score-5 response Prometheus expects | Rewritten as exemplary responses; justification moved to `rationale` |
| E-6 | AX dataset rows carried no evidence, source versions, family, provenance or withheld markers | Added; withheld text never exported; rejected cases refused |
| E-7 | `ProducerConfig` could not record the Coordinator model, retrieval configuration or a dirty-tree patch | Optional fields added |
| E-8 | Fixture inconsistencies: M1 vs M3 page locators for the same pack; M2 vs M4 renderings of table 4.1; M1 placeholder figure text | Registered as separate sources, never mixed; placeholder excluded; reported |

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

### Measured optimization and reliability phase (2026-09-19)

This phase audited all ten layers with local evidence only.

- **Unverified:** live provider latency and quality, AX ingestion, Modal, audible audio and NVDA.
- **The database-backed journey run (E2) did not happen.** Docker Desktop, which
  hosts the disposable PostgreSQL, was paused manually during this phase, and
  the CLI cannot resume it.
- The E2 harness, `api/tests/perf/run_journeys.py`, is written but uncommitted
  and has not been run. Conclusions about PostgreSQL time, TCP transport and
  SQL statements per turn are therefore **unverified**.
- Instruments, baselines, the thresholds declared before each change, and
  before/after tables are in [perf/README.md](perf/README.md), with raw results in
  `docs/team/perf/results/`. That file's working labels map to these IDs:
  C-1 → OPT-1, G-2 → OPT-2, T-1 → OPT-3, G-1 → OPT-4, C-2 → OPT-5.

Ranked findings:

| Rank | ID | Layer | Finding | Class | Evidence | Outcome |
|---|---|---|---|---|---|---|
| 1 | OPT-1 | agent runtime, reliability | A disconnect cancels the turn task, but the Coordinator model call and the Tutor run were separate tasks and kept running. Provider spend continued, and the cancelled Tutor run could persist a question or commit an attempt while the retransmission re-ran the turn. | defect | Fixture (E1): 20/20 model calls orphaned; 20/20 cancelled Tutor runs persisted their question | fixed `4032878`: 0/20 and 0/20 |
| 2 | OPT-2 | guardrails, security | The dispatcher's catch-all handlers logged exception messages. A message can carry private answer data (pydantic prints input values), and the API process configures no redacting formatter. | defect | Regression tests: the marker was present in the logs | fixed `80dac66`: exception type and stack only |
| 3 | OPT-3 | observability | Timed-out export calls were resubmitted while still running. A slow collector received each batch up to 3 times while the counters reported 100% loss. A stalled collector built an unbounded backlog of pending calls, and a shutdown race could count one span lost twice. | defect | Tracing bench (E3), slow collector: 1024 duplicates; 600/600 delivered but counted lost. Stalled: 7 queued calls; 640 duplicates. | fixed `c72a362`: 0 duplicates, exact accounting, at most 1 call in flight |
| 4 | OPT-4 | memory and state, reliability | The generation registry kept every session for the life of the process. Every navigation creates a generation, and every app launch opens a new session. | defect | tracemalloc: 3000 sessions hold 209–419 MB | fixed `8455f30`: 512 sessions retained, 39–75 MB |
| 5 | OPT-5 | instructions and policy (failure semantics) | When the budget ran out inside the Tutor, the student heard "a required service is unavailable" instead of the documented limit reply. | defect | Regression test; reconnect journey 10/10 | fixed `2e4306f` |
| 6 | OPT-6 | agent runtime (docs) | The engine docstring said LangGraph was not installed. | defect (docs) | – | fixed `559cbcf` |
| 7 | OPT-7 | evaluation cost | `GET /result/{id}` is served by the A100 class. A reconciliation lookup after scale-down therefore cold-starts the GPU and loads the model just to read a Modal Dict. | defect, deferred | Code reading, `evaluation/deploy/prometheus_modal.py` | Serve `/result` from a CPU function over the same Dict; needs M4/M2 review and a Modal run |
| 8 | OPT-8 | evaluation cost | By design (pinned by an M4 test), `RunAllowance` counts the inference seconds the endpoint reports. A second cold start within one invocation (a gap longer than the 60 s scale-down) is covered only by the fixed 600 s margin. | risk | Code and the pinned test | M4 decision: count the larger of reported and wall-clock time, or count each such gap as a cold start |
| 9 | OPT-9 | deployment, reliability | The API process calls no `configure_logging` (the worker does). `api.Dockerfile` and `docker-compose.yml` are empty placeholders. | gap | Code reading | M2/M1 deployment work |
| 10 | OPT-10 | agent runtime, cost | Answering a pending check spends 2 Coordinator model decisions (search, then delegate) before deterministic grading. | hypothesis | E1: 2 calls per answer | Live cost of about two provider round trips per answer is unmeasured; routing is an M1/M4 decision |
| 11 | OPT-11 | policy | The repair flow uses the whole approved budget (3 Coordinator decisions + 1 Tutor). One rejected output, or a drop during a decision, ends the turn with the limit reply. | observation | E1 | Budget unchanged (approved 4/6/20) |
| 12 | OPT-12 | observability | There is no span per Tutor provider attempt or for the learning commit (AX plan item 3). | gap | Span inventory | M4 |
| 13 | OPT-13 | memory and state (database) | `session_request_records` and dialogue entries have no retention. The ordered dialogue fetch uses the `session_id` index, then sorts. | hypothesis | Schema reading | Retention policy plus a reviewed migration; measure in E2 |

**Verified, no change needed:**

| Area | Finding |
|---|---|
| Provider SDK retries | Off in both adapters: a 503 costs exactly 1 HTTP call through google-genai and through groq, so no attempt escapes the shared budget. |
| Budget | Constants 4/6/20 in code; prompts carry no budget numbers (the runtime passes the remaining counts). |
| Bounded structures | Turn registry is an LRU of 256. Prompt context: 12 entries or 6000 chars of dialogue, 12000 chars of evidence, the last 6 feedback items. Metric labels come from closed sets. |
| Cancellation elsewhere | Tool dispatch, hybrid retrieval and the worker's job attempt already cancel their children. |
| Speech cache | The key includes the account or source-version scope. |
| LangGraph | Within noise: grounded-repair medians 6.78 ms (LangGraph) vs 6.82 ms (direct), n = 40 each. |
| Tracing cost | About 11 µs per span on the response path, about 0.8 ms per turn, and no wait on export even with a stalled collector. |

**Test results for this phase:**

- `$PY -m pytest -q -p no:cacheprovider` (default suite): 1118 passed, 1 skipped, 47 deselected (was 1106 passed, 1 skipped).
- 12 new regression tests. Each failed on the pre-change code and passes after it:
  - `test_cancellation_scope.py` (2)
  - the Tutor-budget test in `test_coordinator_journey.py` (1)
  - `test_tracing.py` (3)
  - `test_generation_retention.py` (4)
  - `test_unexpected_error_logging.py` (2)
- Tracing tests repeated 15/15 and 10/10 without failure.
- **Not run in this phase:**
  - The integration suite (`-m integration`, 47 tests): it needs the paused PostgreSQL.
  - The .NET client tests: no client code changed, but not re-run.

**Reproduction:** the commands are in [perf/README.md](perf/README.md#reproduction).

**Rollback:** each change is one commit and can be undone with `git revert <sha>`
(`4032878`, `2e4306f`, `559cbcf`, `c72a362`, `8455f30`, `80dac66`). The
instruments and results (`4e2e2af`) change no product code.

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
| `1337360` | D redo | Re-review of slice D: retryable start (`LiveSession`), typed credential rejection, total failure wording, request timeout, re-entrancy, selection keep, path prefix; live tests in the app's real order; mutation-checked |
| `5b4072f`, `73185fe` | docs | Slice D redo record; AX/Modal live readiness check (names only) |
| `c454490`, `a999b1d`, `8ced847` | eval | Source-grounded dataset `netra-grounded-v1`, offline workflow, review package and report |
| `4e2e2af` | perf | Measurement instruments, baselines and predeclared thresholds |
| `4032878` | perf | OPT-1: cancel the model call and Tutor run with their turn |
| `2e4306f` | perf | OPT-5: budget exhausted in the Tutor reported as the turn limit |
| `559cbcf` | docs | OPT-6: engine docstring corrected |
| `c72a362` | perf | OPT-3: one export call in flight, no duplicate delivery, exact loss |
| `8455f30` | perf | OPT-4: generation registry releases idle sessions |
| `80dac66` | perf | OPT-2: unexpected errors logged by type and stack, not message |

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
| Python default, after the optimization phase (`80dac66`); integration not run (PostgreSQL host paused) | 1166 | 1118 | 0 | 1 | 47 |

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
| OPT-7 to OPT-13 | Evaluation `/result` wakes the GPU; the run allowance's cold-start accounting; API logging configuration and empty deployment files; answer routing cost; budget headroom; missing Tutor and learning-commit spans; request and dialogue retention | See "Measured optimization and reliability phase". Each row names its owner. |

## Decisions made (20 September 2026)

Decided by the system lead (M1). Each owner confirms the rows for their area
before building; a contract or migration change still needs both owners'
review. No secret values appear here; they live only in each developer's `.env`
and in AWS Secrets Manager.

| ID | Decision | Consequence / next step | Acts |
|---|---|---|---|
| D-INFRA | PostgreSQL on AWS RDS behind PgBouncer, as M2 deployed (see below) | Replaces the Compose-PostgreSQL recommendation. Compose runs the API, worker and proxy only. | M2, M1 |
| D-LIC | Keep PyMuPDF (AGPL-3.0); Netra's source stays public under AGPL-compatible terms | No LlamaParse adapter is needed | M1 |
| D-AGENT | OpenRouter runs both agents; the Gemini key serves embeddings only | Code change pending: today a set `NETRA_GEMINI_API_KEY` makes the Coordinator call Gemini directly, and a blank env line counts as set | M1 |
| D-AX | AX export through the OpenTelemetry SDK | M2 reviews and adds the OpenTelemetry pins to the lock (install authorization needed), then M1 builds the exporter behind `SpanExporter` (slice E) | M2 → M1 |
| D-CRED | A one-time access code per student, exchanged once by the app; the credential is kept in Windows Credential Manager | Replaces the PKCE browser sign-in of message-flow flow 1 for now; needs an issuance route (M1), the client exchange (M5) and a message-flow update | M1 + M5 |
| D-QUOTA | 20,000 characters of speech per student per day; ledger in PostgreSQL | Migration (M2) and ledger (M1), then register ElevenLabs | M1, M2 |
| D-MIC | M5's INT-11a proposal: separate binary framing (`capture_id`, `sequence`, `end_of_utterance`, `audio/L16;rate=16000`) after `asr.start`; server `asr.transcript`; WinMM capture, no NuGet. **Voice input is the top frontend priority**, ahead of further keyboard/NVDA work | Protocol v1 change: M1 and M5 review; Deepgram adapter (M1), capture (M5) | M1 + M5 |
| M5-VIDEO | WebView2 approved (free SDK, Evergreen runtime) | Version pinned with M5-LOCK; navigation limited to the YouTube embed origin | M5 |
| M3-PIN-1 | Marengo 3.5 and Pegasus 1.5 (newest per TwelveLabs' docs on 20 September) | Copy the exact `model_name` strings from the created index into `NETRA_TWELVE_LABS_*` | M3 |
| M3-YT-ANALYSIS | YouTube videos are analysed through Tunelio, a hosted YouTube downloader (`GET /create` returns a signed, temporary direct link; 6 credits per `/info`, 10 per `/create`) | Risks accepted by the owner: downloading breaks YouTube's Terms of Service and may infringe copyright on lecture videos; the service works around YouTube's bot checks and can stop without notice; students' video choices go to a third party. Guardrails: off unless `NETRA_TUNELIO_API_KEY` is set; the worker copies the video into the private S3 bucket and TwelveLabs reads it through a presigned URL (the upload path, M3-MEDIA-URL); results are labelled AI descriptions. Considered and not chosen: Gemini's official YouTube-link input (no download; public videos only; preview feature). | M3, M2 |
| M1-M3-V | A generated (Pegasus) description is shown labelled as an AI description; it is never verified evidence and never grades an answer | Matches the evidence ledger's GENERATED handling | M1, M3 |
| P-1 | A check question is asked only when its answer is supported by the cited evidence; otherwise it is skipped (fail closed) | Implement `validate_draft_is_grounded` | M4 (+M3) |
| P-2 | A declined check records nothing; history shows "studied, not tested" | Current behaviour kept | M4, M5 |
| P-3 | Evidence deleted or re-versioned while a question waits: do not grade, keep the question, tell the student | | M4, M2, M1 |
| P-4 | `source_version_id` is a UUID string; `evidence_version` is carried on `Evidence` | Already implemented (INT-03) | — |
| D3 / D-CONCEPT | Adopt M4's factual history records and a curated concept catalog, projected before attempts | M2 migrations, M4 services | M4, M2 |
| D-BUDGET | Keep 4 decisions / 6 tools / 20 s; persist budget use per request id | Migration (M2), accounting (M1) | M1, M2 |
| INT-10c | Upload and processing status are read by polling an HTTP job-status route | Job schema (M2), routes (M1), screens (M5) | M2, M1, M5 |

## AWS infrastructure (M2)

Terraform: `infrastructure/terraform` (commits `f0a20fe`, `8b0da16`; PR #17).
The commit calls it "unapplied", but M2 applied it: on 18 September M2 reported
the PgBouncer instance running with SSM online, RDS available, the application
database secret created (16 September) and the documents bucket present.
Terraform state exists only on M2's machine (`*.tfstate` is git-ignored).

| Resource | Configuration |
|---|---|
| Network | VPC 10.42.0.0/16 in ap-south-1; 2 public subnets, 2 private database subnets |
| PgBouncer | EC2 t4g.micro, SSM-managed, no inbound ports; listens on the instance's 127.0.0.1:6432; `pool_mode = session` (asyncpg prepared statements are safe), 10 server connections per pool, 50 clients |
| RDS | PostgreSQL 17.11, db.t4g.micro, private, encrypted, single-AZ, reachable only from PgBouncer |
| Secrets | Application database password in Secrets Manager; RDS-managed master secret |
| S3 | Private documents bucket: public access blocked, AES-256, versioning on |
| Evaluator GPU | Disabled (`enable_prometheus_gpu = false`); Modal is the evaluator |

Identifiers (instance ID, RDS endpoint, bucket name) come from `terraform output`
and belong in each developer's `.env`, not in this file. A developer machine
keeps an SSM port forward open (AWS CLI with the Session Manager plugin) and
points `NETRA_DATABASE_URL` at `127.0.0.1:6432`:

```
aws ssm start-session --region ap-south-1 --target <pgbouncer_instance_id> --document-name AWS-StartPortForwardingSession --parameters "portNumber=6432,localPortNumber=6432"
```

S3 access uses the developer's AWS CLI login through boto3; no keys in `.env`.

Gaps found while reading the Terraform (20 September):

- `backup_retention_period = 0`: RDS has no automated backups.
- No application host: nothing provisions an instance for the API and worker,
  and the Compose file and both Dockerfiles are still empty.
- Migration state on RDS is unverified: run `alembic -c api/alembic.ini current`
  through the tunnel.
- `deletion_protection = false` (a development setting).
- Each API process's default SQLAlchemy pool (5 + 10 overflow) can exceed
  PgBouncer's 10 server connections in session mode; extra clients wait rather
  than fail. Revisit before real load.

## Decisions needed

| ID | Decision | Recommendation | Blocks |
|---|---|---|---|
| D-NEO4J | Real Cypher never executed | Authorize a disposable local `neo4j:5.26.30` container, or a free Aura instance | Neo4j verification |
| D-HOST | No application host for the API and worker | One EC2 instance in the existing VPC running Compose (API, worker, nginx) with an IAM role for the bucket and the database secret | Deployment |
| D-BACKUP | RDS automated backups are off | Set a retention period (for example 7 days) before real student data | Production data |
| D-TFSTATE | Terraform state exists only on M2's machine | An S3 backend with state locking | Anyone but M2 changing infrastructure |
| D-OTEL-PINS | Exact OpenTelemetry versions (D-AX) | M2 review under the lock cutoff | AX exporter |
| D-MODAL-PINS | 7 pins in `evaluation/deploy/prometheus_modal.py` are `PENDING_M2_REVIEW` | M2 review; fix OPT-7/OPT-8 before GPU use | Modal judge |
| INT-11b / INT-11c | ElevenLabs output media type; total audio frame size limit | M1 with M5 | Speech output |

## Independent work that can proceed

Slices C–E need no live provider or new product policy for their local parts.
