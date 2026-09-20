# Running Netra on this laptop, yourself

Everything below was run on your machine on 20 September 2026. Commands are for
**PowerShell**, from `C:\Users\Arshad\AppData\Local\Temp\netra-int`.

> **The database is in RAM.** The test Postgres uses `tmpfs`. Stop that container and
> you lose the schema, every account and every uploaded document. Re-run steps 1–2.

---

## 0. Once per shell: load the environment

    cd C:\Users\Arshad\AppData\Local\Temp\netra-int
    . .\load-env.ps1

The API can read `.env` itself (`uvicorn --env-file .env`). The **worker** and the
**access-code CLI** cannot — they read the process environment, so load it first.

This worktree's `.env` carries three local overrides your main `.env` does not:

| Setting | Local value | Why |
|---|---|---|
| `NETRA_DATABASE_URL` | local Postgres :55432 | no SSM tunnel to RDS needed |
| `NETRA_STORAGE_PROVIDER` | `local_fixture` | **no AWS needed**; bytes go to `.local/sources` |
| `NETRA_OPENROUTER_COORDINATOR_MODEL` | `openai/gpt-oss-120b` | see "Models" below |
| `NETRA_ELEVENLABS_VOICE_ID` | `Xb7hH8MSUJpSbSDYk0k2` | see "Speech" below |

Paths in `.env` need **forward slashes**. Bash eats backslashes when sourcing.

---

## 1. Postgres

    docker compose -f infrastructure\compose\docker-compose.test.yml up -d

Check: `docker ps` shows `compose-postgres-test-1` on `127.0.0.1:55432`.

## 2. Migrations

    $env:PYTHONPATH="api/src"
    .venv\Scripts\python.exe -m alembic -c api/alembic.ini upgrade head

Check: `... current` prints `0012_m3_video_assets (head)`.

## 3. API

    $env:PYTHONPATH="api/src;worker/src"
    .venv\Scripts\python.exe -m uvicorn --env-file .env --app-dir api/src --factory netra_api.main:create_app --host 127.0.0.1 --port 8000

Check, in another shell:

    curl http://127.0.0.1:8000/health/live

All 11 entries under `registered` should be `true`. Any `false` means that capability
is unconfigured and its routes answer 503 rather than guessing.

## 4. Worker

In a third shell (load the environment first — step 0):

    $env:PYTHONPATH="api/src;worker/src"
    .venv\Scripts\python.exe -m netra_worker.main

It logs JSON. `FileNotFoundError` on old jobs is expected: those were uploaded when
storage was S3, and their bytes are not on this disk.

## 5. A student account

    $env:PYTHONPATH="api/src"
    .venv\Scripts\python.exe -m netra_api.identity.provisioning --access-code --code-valid-days 7 --credential-valid-days 120

Prints one access code and an `account_id`. **The code works once.** Reuse gives 401,
by design. To add a second code to the *same* student, pass
`--account-id <the account_id>` — otherwise every code creates a new account with an
empty library, which is why your second upload can look like it vanished.

## 6. Upload a PDF and read it

    $env:PYTHONIOENCODING="utf-8"
    .venv\Scripts\python.exe demo-scripts\_journey.py <ACCESS-CODE>

Exchanges the code, creates a session, uploads `docs/One Dinner Four Kitchens.pdf`,
retransmits it to prove idempotency, polls until ready, lists the library.
Expected: `202`, then `200` with the same `job_id`, then `state=ready` in about 12 s.

## 7. Ask the Tutor

    .venv\Scripts\python.exe demo-scripts\_tutor.py <ACCESS-CODE> "According to the kitchen analogy, what is an agent?"

Expected: a grounded answer plus binary MP3 frames (that is the speech output).
A question the evidence does not support is refused on purpose — that is the design,
not a failure.

## 8. The WPF client

    dotnet build client\Netra.sln --no-restore

0 errors, ~5 s. Packages are already restored and the WebView2 runtime is installed
(153.0.4234.32), so this works offline. To launch:

    $env:NETRA_API_ENDPOINT="ws://127.0.0.1:8000/v1/ws"
    dotnet run --project client\src\Netra.Desktop

**I built this but never launched it.** Treat step 8 as unverified.

## 9. Tests

    .venv\Scripts\python.exe -m pytest -p no:cacheprovider --ignore=tests/test_integration.py -q -m "integration or not integration"

1906 passed, 62 skipped, ~125 s (20 September 2026, after the AX exporter and the
Ragas-style metrics added 32 tests).

---

## Models

Measured on your account, same question, same evidence:

| Coordinator model | Result |
|---|---|
| `google/gemini-3.8-flash` (your default) | **4/4 decisions rejected** `decision_not_json` |
| `anthropic/claude-haiku-4.5` | **4/4 rejected**, never called the search tool |
| `openai/gpt-oss-120b` | valid decisions, calls `search_sources`, answers |

Only the third works. Changed in this worktree's `.env` only — the model choice is
decision D-AGENT, so promoting it upstream is yours.

Turn cost, measured: one model decision 1.3–3.0 s; embeddings ~1.2 s; Pinecone ~1.8 s;
a full retrieval round ~4.6 s. The budget is 20 s and 4 decisions, so a model that
wastes decisions on malformed output exhausts the turn. OpenRouter is not slow.

## Speech

Your voice id `21m00Tcm4TlvDq8ikWAM` (Rachel) is a **library** voice. On a free
ElevenLabs tier the API refuses it:

    HTTP 402  paid_plan_required
    "Free users cannot use library voices via the API."

Premade voices work fine on free tier. This worktree uses `Xb7hH8MSUJpSbSDYk0k2`
(Alice, "Clear, Engaging Educator"). Verified: 200 OK, 56 KB of MP3, 0 failures in a
live turn. Your key and quota were never the problem (0 of 10,000 characters used).

## What is set, and what is not

Every setting the local student path needs is set. 27 `NETRA_*` keys in your `.env`
are read by **no code in this repository**:

- `NETRA_TWELVE_LABS_*`, `NETRA_TAVILY_*`, `NETRA_TUNELIO_API_KEY` — no settings class
  reads them yet (M3's media pipeline is not wired to configuration).
- `NETRA_AX_*` — still unread. The exporter uses the names AX documents,
  `ARIZE_SPACE_ID` / `ARIZE_API_KEY` / `ARIZE_PROJECT_NAME`, which your `.env`
  already carries. `NETRA_TRACING_MODE=ax` now works (see the tracing section).
- `NETRA_EVAL_*`, `MODAL_TOKEN_*` — read only by `evaluation/scripts`, not the API.
- `NETRA_API_ENDPOINT` — read by the WPF client, from its own process environment.

Setting them changes nothing today. They are not broken; they are ahead of the code.

## Not verified on this machine

- Voice input (Deepgram registers; no microphone turn attempted).
- The WPF app actually running, NVDA, keyboard navigation.
- Tesseract OCR against a scanned PDF (v5.5.3 is installed; the text-layer path does
  not use it).
- Modal judge, S3, RDS. (Neo4j and AX export were verified on 20 September
  2026 — see the tracing section below.)

---

# Video wiring (added 20 September 2026)

## What is now live

`GET /health/live` gained three entries and the Coordinator gained a tool:

| Entry | Meaning |
|---|---|
| `video_evidence` | stored video evidence is readable (needs no provider at all) |
| `video_analysis` | every TwelveLabs pin is configured (this does NOT prove the key works) |
| `youtube_discovery` | the Tavily provider was constructed |
| `tool:search_lecture` | the Coordinator is now offered the lecture-search tool |

Your existing `NETRA_TWELVE_LABS_*` and `NETRA_TAVILY_*` keys are no longer dead:
23 new settings read them. No new env variables are needed — the names already
matched.

## What is NOT done

- **Nothing produces video evidence yet.** The worker still registers five
  handlers, all PDF. `jobs/multimedia/video.py` and `recording.py` are written
  and unit-tested but unregistered, so no video is ever indexed or described.
- **No route adds a video to a library.** U1's upload route is PDF-only by
  contract (C5 document ingestion). A YouTube video cannot be attached to a
  source version through any API today.
- **Discovery has no wire contract.** The provider is constructed but nothing
  exposes it; the WPF client's `IVideoDiscoveryService` still returns stubs and
  says so in a comment.
- **The TwelveLabs key returned 401** when tested, so analysis has never run
  live. The pins (marengo3.0/pegasus1.5 in your main `.env`) are also
  unverified against the index, which needs a working key to read.

So the read side and the composition are complete and tested; the producing
side and the two contracts are not. Video evidence that exists in the database
is served, authorized and cited correctly — but nothing puts it there yet.


---

# Tracing, Neo4j and evaluation (added 20 September 2026)

## 10. Neo4j (the learning projection)

    docker run -d --name netra-neo4j -p 127.0.0.1:7687:7687 -p 127.0.0.1:7474:7474 `
      -e NEO4J_AUTH=neo4j/netra_local_only neo4j:5.26.30

It is a **rebuildable projection**: PostgreSQL stays authoritative, so losing this
container loses nothing. The worker only uses it when these are in the environment,
and they are **not in `.env` yet** — add them, or the worker silently runs without it:

    NETRA_NEO4J_URI=bolt://127.0.0.1:7687
    NETRA_NEO4J_USER=neo4j
    NETRA_NEO4J_PASSWORD=netra_local_only

Check: `LearningProjectionSettings().configured` is `True`. Verified 20 September
2026 — connected, wrote a node, read it back, deleted it.

## 11. Arize AX tracing

Add one line to `.env` (the `ARIZE_*` credentials are already there):

    NETRA_TRACING_MODE=ax

Then restart the API and worker. Check:

    curl http://127.0.0.1:8000/health/telemetry

`tracing_enabled` is `true` and `configuration_errors` is `0`. After a turn,
`exported` should equal `ended`, with `export_failures` and the `dropped_*`
counters at 0.

Verified on 20 September 2026 through a real turn: **41 spans created, 41
exported, 0 failed, 0 dropped**, every batch HTTP 200. Two caveats:

- HTTP 200 is the exporter's acknowledgement, **not proof AX ingested anything**.
  Confirm in the AX UI. Spans land in the project named by `ARIZE_PROJECT_NAME`,
  which is currently `netra-notes` — change it if you want the app separate from
  the demo slice.
- Export is background only. A slow or dead AX cannot delay a turn or STOP; it
  shows up as `export_failures` and dropped spans, never as a student-visible error.

No OpenTelemetry package is installed and none is needed: the exporter sends
OTLP/HTTP JSON with the `httpx` already in the runtime.

## 12. Ragas-style evaluation

    .venv\Scripts\python.exe evaluation\scripts\run_ragas_style.py

Needs `PYTHONPATH=evaluation/scripts`. On `netra_p3_answer_golden_v1` this prints
faithfulness 1.000 (n=6/6) and answer_relevancy 0.675 (n=6/6); the two context
metrics report `no_relevance_labels` because that file carries none.

**The Ragas package is not installed and must not be** (runtime-baseline.md): every
release pulls the full LangChain stack plus `openai`, and 0.2.3–0.4.3 carry an
unfixed SSRF advisory. These are the repository's own deterministic metrics. They
are lexical proxies — they cannot see paraphrase, and they are not comparable with
a real Ragas run or with Prometheus ordinal scores.

The real-lecture retrieval benchmark still **cannot run here**:

    RuntimeError: locked dataset references missing canonical source/version rows

That is the fail-closed check working. The locked dataset points at source rows
that this tmpfs database no longer holds; re-ingest those lectures to run it.
