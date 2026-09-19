# Arshad's prompt — M1 and M4

Paste everything below the line into Claude Code (Opus 5), started at the root of
your own clone of https://github.com/The-Trinetras/netra.

---

You are the Claude Code session for Arshad, Netra's system lead. For this
completion push Arshad owns **M1** (Coordinator, identity and sign-in, routing,
transport, server-side speech, tracing) and, because Dhanishka is unavailable,
**M4** (Tutor, optional checks, factual history, Neo4j projection, evaluation).
Ashlin (data, jobs, infrastructure, media pipeline, Modal) and Arun (desktop
client, YouTube search, MathML) work in parallel in their own clones with their
own sessions. Build working, tested code; do not stop at plans.

## Read first, in order

1. `CLAUDE.md`, `AGENTS.md`, `README.md`.
2. `docs/team/prompts/completion/README.md` — the plan, steps and working rules.
3. `docs/team/integration-status.md` — "Decisions made (20 September 2026)" are
   final; do not reopen them. Also "AWS infrastructure (M2)", "Decisions needed",
   "Open items found".
4. `docs/team/integration-checklist.md` (definition of done),
   `docs/architecture/current-scope.md`, `message-flow.md`, `agent-boundaries.md`,
   `arize-ax-integration.md`, `model-evaluation-plan.md`.
5. `.claude/rules/coordinator.md`, `.claude/rules/learning.md`,
   `docs/team/handoffs/M1.md`, `docs/team/handoffs/M4.md`.
6. The code each item touches. Source and tests are the truth; handoff notes and
   this prompt can be out of date — if they disagree with the code, say so in one
   line and follow the code.

## Ground rules

- Exactly two agents, Coordinator and Tutor. Turn budget 4 model decisions, 6
  tools, 20 seconds, shared across retries and delegation. STOP is immediate;
  only an accepted final transcript submits a turn. Providers stay behind
  adapters. Unimplemented authorization or persistence fails closed.
- `slice/` is the kit spine: never modify it. `demo/notes/` is the event slice:
  out of scope.
- One branch per item, `arshad/<item-id>`, from the latest `main`. Commit when the
  item's tests pass, with a short plain message ending in the Co-Authored-By line
  your harness supplies. Never push, force, reset or discard; Arshad pushes.
- Shared contracts (`shared/contracts/**`) and migrations need the other affected
  person's review: say who in your report.
- Tests: `python -m pytest -p no:cacheprovider --ignore=tests/test_integration.py`
  (the ignored file spends the OpenRouter key). Database tests need
  `NETRA_TEST_DATABASE_URL` and `-m integration`. Every behaviour you add gets a
  test that fails without it; check that by breaking the code once and restoring it.
- Never read `.env`, print keys or put secrets in code, logs, docs or commits.
- Ask Arshad first, and state the cost, before: any dependency or lock change, any
  live provider call, anything on AWS/RDS, anything in Ashlin's or Arun's areas.

## Work queue

Work top to bottom. Skip an item that is blocked, say by whom, and take the next.

### Step 1 — contracts (day one, with Arun and Ashlin)

- **C1 Microphone protocol (D-MIC).** Add to protocol v1: an `asr.start` text
  message; client binary audio frames with `capture_id`, `sequence`,
  `end_of_utterance`, `audio/L16;rate=16000`; a server `asr.transcript` message
  (`capture_id`, `transcript_id`, `text`, `is_final`). Schemas, examples, Python
  models and validation tests. Arun reviews the client side.
- **C2 Access-code exchange (D-CRED).** A route that exchanges a one-time code for
  a device credential (shape, errors, expiry, replay behaviour) and an operator
  command that issues codes (extend `identity/provisioning.py`). Arun reviews.
- **C3 Speech wire details.** ElevenLabs media type (INT-11b; `mp3_44100_128` is
  `audio/mpeg`) and the total audio frame size limit with its violation behaviour
  (INT-11c). Arun reviews.
- Review Ashlin's job-schema and evidence-payload contracts and Arun's YouTube
  search and video-time contracts from the Coordinator side.

### Step 2 — foundations

- **A1 API logging** (OPT-9): configure logging at API start like the worker does;
  no secrets or message bodies in logs.
- **A2 Sign-in:** access-code issuance and exchange on C2, stored hashed like
  existing credentials. Uses Ashlin's migration if a table is needed.
- **A3 Persisted turn budget** (D-BUDGET): budget use keyed by request id on
  Ashlin's migration; a retransmission after restart cannot get a fresh budget.
- **A4 Speech output:** a PostgreSQL quota ledger of 20,000 characters per student
  per day (D-QUOTA) replacing `UnconfiguredQuotaLedger`; settings fields for
  `NETRA_ELEVENLABS_*`; register the existing ElevenLabs adapter in production
  composition; quota exhaustion fails closed with an accessible message.

### Step 3 — voice and the Tutor

- **B1 Voice input:** a Deepgram adapter behind `speech/recognition.py`'s
  `RecognitionAdapter` (`speech/providers/deepgram.py` is a stub today), the C1
  messages in the WebSocket transport, `accept_final_transcript` as the only way
  to a turn, duplicate finals ignored, STOP and cancellation fenced. Voice input is
  the team's top frontend priority: pair with Arun as soon as Arun's capture works.
- **B2 Optional checks** (P-1, P-3): implement `validate_draft_is_grounded`
  (`learning/quiz/validator.py`) so a question is asked only when its answer is
  supported by the cited evidence; a question generator on the OpenRouter Tutor
  model behind an adapter; register it in `production_dependencies`; evidence
  changed while a question waits means no grade, keep the question, tell the
  student.
- **B3 Factual history** (D3, P-2): services for delivered activity, answers,
  stated reasoning, feedback and assistance on Ashlin's migration; a declined
  check records nothing and history shows "studied, not tested". No mastery labels.
- **B4 Concept catalog and Neo4j** (D-CONCEPT, D-NEO4J): curated concepts projected
  before attempts; run the projection against Ashlin's Neo4j and fix what real
  Cypher reveals.

### Step 4 — video on the Coordinator side

- **V1** Register the M3 services Ashlin wires (`figures`, `video_evidence` via
  `multimedia/video/stored_service.py`) in `production_dependencies`, so their
  tools reach the Coordinator. Pegasus output is labelled an AI description and
  never counts as verified support or grades an answer (M1-M3-V).
- **V2** Lecture questions answered with timestamps; pause-and-describe on the
  server side of Arun's video-time contract.

### Step 5 — tracing and evaluation

- **T1 AX exporter** (D-AX) after Ashlin lands the OpenTelemetry pins: behind
  `platform/tracing.py`'s `SpanExporter`, reading `NETRA_AX_*`, bounded background
  export, visible loss, `tracing_mode=ax` working. No student turn waits on AX.
- **T2** Tutor and learning-commit spans (OPT-12) and trace reconciliation for
  evaluation runs (INT-12).
- **T3 Evaluation:** prepare the review batch in `evaluation/review/` for humans to
  label (at least 10 per criterion; you never invent labels), import real Netra
  outputs, run the judge on Ashlin's Modal deployment, upload to AX, and produce
  the paired comparison. Meet all 8 AX gates in the checklist.

### Step 6 — acceptance

- **E1** With Ashlin and Arun, run every integration-checklist item on the deployed
  build; record date, commit and evidence in `integration-status.md`; fix defects
  in your areas and file the rest to their owner.

## After each item, report

Item id; branch and commit; files changed; exact commands and results; what you
broke to prove the tests work; what ran on mocks, real local services or live;
decisions or blockers and who owns them; `git diff --stat`. Update the matching row
in `docs/team/integration-status.md` and your handoff note. Then continue with the
next unblocked item unless Arshad says stop. Never go past a step that needs
Arshad's yes without asking.
