# Completion push: three people, three Claude Code sessions

Prepared 20 September 2026 from [integration status](../../integration-status.md)
("Decisions made", "AWS infrastructure (M2)", "Decisions needed", "Open items
found"). The earlier [M1–M5 prompts](../README.md) and the
[integration playbook](../../integration-playbook.md) remain history; this pack
replaces them for the remaining work.

**Done means** every box in the [integration checklist](../../integration-checklist.md)
is ticked with real evidence (16 product checks and 8 Arize AX gates), each run
recorded in integration status with date, commit and what was live versus mocked.
Two boxes need people, not code: keyboard/NVDA/voice sessions with blind or
low-vision students, and human labels for the evaluation cases.

## Who does what

Dhanishka (M4) and Sumedhaa (M5) are unavailable for this push, so their areas
are covered as below. Each person runs one Claude Code session (Opus 5) in their
own clone, with the prompt from this folder.

| Person | Prompt | Areas |
|---|---|---|
| Arshad | [arshad.md](arshad.md) | M1 Coordinator, identity/sign-in, transport, server speech (ElevenLabs, Deepgram), tracing; **M4** Tutor, optional checks, factual history, Neo4j projection, evaluation; the **Modal judge** (Ashlin reviews its version pins) |
| Ashlin | [ashlin.md](ashlin.md) | M2 data, migrations, jobs, uploads, retrieval; AWS and deployment; the **M3 media pipeline** (Tunelio, TwelveLabs, worker media jobs) |
| Arun | [arun.md](arun.md) | **M5 desktop client** (voice input first, sign-in, upload, exploration, video player, playback, accessibility); M3 user-facing pieces (YouTube search route, MathML, media label review) |

## What remains

| Area | Remaining work | Owner |
|---|---|---|
| Contracts | Microphone protocol (D-MIC), access-code exchange (D-CRED), speech media type and frame limit (INT-11b/c), client evidence payload (M5-EVIDENCE), job schema (empty today), YouTube search and video time/readiness payloads | All three, day one |
| Identity | Access-code issuance and exchange; session resume after an app restart (D-open-2) | Arshad, Arun |
| Speech | ElevenLabs switched on with the 20,000-character daily ledger; Deepgram voice input (only final transcripts submit) | Arshad, Arun |
| Budget | Turn budget saved per request so a restart cannot reset it (D-BUDGET) | Arshad, Ashlin |
| Learning | Optional checks (`validate_draft_is_grounded` raises today), factual history records (D3), concept catalog, real Neo4j run | Arshad, Ashlin |
| Data | OpenTelemetry pins, migrations applied on RDS, upload and job-status routes, live Gemini embeddings and Pinecone | Ashlin |
| Video | TwelveLabs index on Marengo 3.5 + Pegasus 1.5, Tunelio → S3 → TwelveLabs pipeline, worker media jobs, Coordinator video/figure tools, WebView2 player, YouTube search | Ashlin, Arshad, Arun |
| Client | Sign-in screen, voice capture, real audio, upload and status, figure/table/equation exploration, MathML, hotkeys | Arun |
| Tracing and evaluation | AX exporter, Tutor spans, trace reconciliation; Modal judge deployed; labelled dataset; AX paired comparison | Arshad (Ashlin reviews Modal pins) |
| Deployment | API and worker images, Compose, an application host, PgBouncer reachable from it, RDS backups, shared Terraform state, TLS | Ashlin |
| People | NVDA/keyboard/voice sessions with students; evaluation labels | Arun leads; everyone |

## Step by step

### Step 0 — set up (each person, once)

1. Clone https://github.com/The-Trinetras/netra and work only in your own clone.
2. Python 3.13 virtual environment, then `pip install -r requirements.txt`.
   Arun also needs the .NET SDK 10.0.401 (`client/global.json`) and NVDA.
3. Get `.env` from Arshad **outside chat and outside Git** (a password manager or
   an encrypted note). Never paste keys into Claude or commit them.
4. For RDS, open the SSM port forward in integration status and leave it open.
5. For database tests, start the throwaway Docker PostgreSQL
   (`infrastructure/compose/docker-compose.test.yml`).
6. Start Claude Code at the repository root and paste your whole prompt file.

### Step 1 — contract day (all three together, first)

The parallel work only stays fast if the shared wire shapes are settled first.
Each contract is drafted by one session, reviewed by the other two people, and
merged before anyone builds on it. Everyone else builds against the agreed shape
with labelled fakes until the producer lands.

| Contract | Drafts | Reviews |
|---|---|---|
| Microphone protocol (`asr.start`, audio frames, `asr.transcript`) | Arshad | Arun |
| Access-code exchange route | Arshad | Arun |
| Speech media type and total frame size | Arshad | Arun |
| Job schema, upload and job-status routes | Ashlin | Arshad, Arun |
| Client evidence payload (figures, tables, equations, AI-description flag) | Ashlin | Arshad, Arun |
| YouTube search and video time/readiness payloads | Arun | Arshad, Ashlin |

### Step 2 — foundations (parallel)

- **Arshad:** API logging, access-code routes, persisted turn budget, speech quota
  ledger and ElevenLabs switched on.
- **Ashlin:** OpenTelemetry pins in the lock, migrations (budget, quota, history,
  concepts, access codes) applied on RDS, Dockerfiles, Compose, application host.
- **Arun:** NuGet lock, sign-in screen, microphone capture on the new protocol.

Done when: all three merge, the full suite passes, and API plus worker start in
Docker against RDS.

### Step 3 — voice and the core journey live (parallel)

- **Arshad:** Deepgram voice input, optional checks, history records, concept
  catalog and Neo4j projection.
- **Ashlin:** upload route and live ingestion to S3, live embeddings and Pinecone.
- **Arun:** push-to-talk end to end, real speech playback, upload screen, real
  figure/table/equation exploration and MathML.

Done when: on real models and data a student signs in, asks by voice, hears an
answer, explores a table, answers a check question, presses STOP and returns to
the exact reading position.

### Step 4 — video

- **Ashlin:** TwelveLabs index recreated on the decided models, Tunelio → S3 →
  TwelveLabs job, worker media jobs, private media links.
- **Arshad:** video and figure tools registered in the Coordinator, AI
  descriptions labelled, lecture questions with timestamps.
- **Arun:** YouTube search route and screen, WebView2 player with time capture,
  pause-and-describe and exact resume.

Done when: an uploaded lecture and a YouTube lecture can both be found, played,
paused, described and questioned, with the actual player time recorded.

### Step 5 — tracing and evaluation

- **Arshad:** Modal pins (Ashlin reviews), the two cost fixes (OPT-7, OPT-8), judge
  deployed with a spend limit; AX exporter, spans, trace reconciliation, labelled
  dataset, real outputs, judge runs, AX paired comparison.
- **Arun:** STOP-to-silence and responsiveness measured with tracing on and off.

Done when: all 8 AX gates in the checklist pass.

### Step 6 — deploy and people

- **Ashlin:** deploy to the application host, TLS, backups and a restore test.
- **Arun:** sessions with blind or low-vision students using NVDA, keyboard and
  voice; fix what they hit; test again.
- **Arshad:** run every checklist item on the deployed build and record it.

Done when: every checklist box is ticked with evidence in integration status.

## Working rules for three parallel sessions

- **Branches:** one per work item, named `<person>/<item-id>` (for example
  `arun/F3-voice-capture`), from the latest `main`. Small pull requests; rebase
  daily; one other person reviews. Contract and migration PRs need every
  affected person.
- **Pushing:** Claude cannot push (`.claude/settings.json`). The person reviews
  the commits, pushes the branch and opens the PR.
- **Tests:** `python -m pytest -p no:cacheprovider --ignore=tests/test_integration.py`.
  The ignored file makes three live model calls on the OpenRouter key; run it only
  on purpose. Database tests: set `NETRA_TEST_DATABASE_URL` and add `-m integration`.
  Client: `dotnet test client/Netra.sln`.
- **Evidence:** every item reports what ran on mocks, on real local services and
  live; update integration status when it merges.
- **Secrets:** never in chat, code, logs, docs or commits.
- **Daily sync, 15 minutes:** what merged, what is blocked on whom, what is next.

## What only the people can do

- Give each session explicit yes/no for: dependency or lock changes, live
  provider calls (the session states the expected cost), AWS or RDS changes
  (Terraform plans are reviewed before apply), the Modal deploy, database
  migrations on RDS.
- Recreate the TwelveLabs index on Marengo 3.5 + Pegasus 1.5 in the dashboard.
- Set the Modal spend limit; choose the domain for TLS.
- Push, review and merge pull requests.
- Recruit testers (blind or low-vision students with NVDA) and label evaluation
  cases.

## Asking your session

- "Start" — it reads, reports its plan for the first item and begins.
- "Next" — the next unblocked item in its queue.
- "Yes, you may …" — a specific authorization, for example "Yes, you may run
  `uv lock` for the OpenTelemetry pins" or "Yes, one live Deepgram check".
- "Stop" — finish the current step, commit if green, report.
