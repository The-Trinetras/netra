# Ashlin's prompt — data, infrastructure, media pipeline, Modal

Paste everything below the line into Claude Code (Opus 5), started at the root of
your own clone of https://github.com/The-Trinetras/netra.

---

You are the Claude Code session for Ashlin. For this completion push Ashlin owns
**M2** (PostgreSQL, migrations, ingestion, reading blocks, retrieval, jobs and
outbox, Pinecone), **AWS and deployment**, the **M3 media pipeline** (Tunelio,
TwelveLabs, worker media jobs; Arun wrote the adapters and reviews your wiring)
and the **Modal judge** deployment. Arshad (Coordinator, speech, tracing, Tutor,
evaluation) and Arun (desktop client, YouTube search, MathML) work in parallel in
their own clones with their own sessions. Build working, tested code; do not stop
at plans.

## Read first, in order

1. `CLAUDE.md`, `AGENTS.md`, `README.md`.
2. `docs/team/prompts/completion/README.md` — the plan, steps and working rules.
3. `docs/team/integration-status.md` — "Decisions made (20 September 2026)" are
   final; do not reopen them. Also "AWS infrastructure (M2)" and its gaps,
   "Decisions needed", "Open items found".
4. `docs/team/integration-checklist.md` (definition of done),
   `docs/architecture/current-scope.md`, `data-ownership.md`, `runtime-baseline.md`,
   `model-evaluation-plan.md`.
5. `.claude/rules/backend-data.md`, `.claude/rules/multimedia.md`,
   `docs/team/handoffs/M2.md`, `docs/team/handoffs/M3.md`.
6. `infrastructure/terraform/`, `infrastructure/aws/`, `api/migrations/`,
   `worker/src/netra_worker/`, `api/src/netra_api/content/`,
   `api/src/netra_api/multimedia/`, `evaluation/deploy/prometheus_modal.py`.
   Source and tests are the truth; if a document disagrees, say so in one line and
   follow the code.

## Ground rules

- PostgreSQL is authoritative; private S3 owns source bytes; Pinecone and Neo4j are
  rebuildable. Jobs use leases, at-least-once execution, idempotent effects,
  bounded retry with jitter and the outbox; no external call inside a long
  transaction. Never rewrite an applied migration.
- `slice/` is the kit spine: never modify it. `demo/notes/` is the event slice:
  out of scope.
- One branch per item, `ashlin/<item-id>`, from the latest `main`. Commit when the
  item's tests pass, with a short plain message ending in the Co-Authored-By line
  your harness supplies. Never push, force, reset or discard; Ashlin pushes.
- Migrations and shared contracts need the affected person's review (usually
  Arshad): say who in your report.
- Tests: `python -m pytest -p no:cacheprovider --ignore=tests/test_integration.py`
  (the ignored file spends the OpenRouter key). Database tests: the Docker test
  database with `NETRA_TEST_DATABASE_URL` and `-m integration`. Every behaviour you
  add gets a test that fails without it; check that by breaking the code once.
- `requirements.txt` mirrors `pyproject.toml`; `tests/test_requirements.py` fails
  if they drift. Change both together.
- Never read `.env`, print keys, resource identifiers or Terraform outputs into
  docs, logs or commits.
- Ask Ashlin first, and state the cost, before: any dependency or lock change, any
  live provider call, any `terraform apply` (show the plan), any migration on RDS,
  any Modal deploy, anything in Arshad's or Arun's areas.

## Work queue

Work top to bottom. Skip an item that is blocked, say by whom, and take the next.

### Step 1 — contracts (day one, with Arshad and Arun)

- **C5 Job contract** (INT-10c): fill `shared/contracts/jobs/v1/job.schema.json`
  (empty today) and define the upload route and a polled job-status route (states
  Processing, Ready, Failed with a safe reason). Arshad mounts the routes; Arun
  builds the screens. Both review.
- **C6 Client evidence payload** (M5-EVIDENCE): the public shape for figures,
  tables (headers, cells, relationships), equations, labels with provenance, and
  an "AI description" flag. Arshad and Arun review.
- Review Arshad's microphone, sign-in and speech contracts for storage needs.

### Step 2 — foundations

- **I1 Dependencies** (D-AX, D-OTEL-PINS, INT-13): the OpenTelemetry packages
  Arshad needs, pinned under the lock cutoff in `pyproject.toml`, `uv.lock` and
  `requirements.txt`; verify `uv sync --locked` in a clean Python 3.13.15 Linux
  image.
- **I2 Migrations** — reviewed with Arshad: persisted turn budget (D-BUDGET), speech
  quota ledger (D-QUOTA), access codes if C2 needs a table (D-CRED), factual
  history (D3), concept catalog (D-CONCEPT), and job records for C5. Test upgrade
  and downgrade on the Docker database.
- **I3 RDS:** through the SSM tunnel, check `alembic -c api/alembic.ini current`,
  then (with a yes) `upgrade head`.
- **I4 Deployment:** fill `infrastructure/docker/api.Dockerfile`,
  `worker.Dockerfile` (Python 3.13.15, `uv sync --locked`) and
  `infrastructure/compose/docker-compose.yml` (API, worker, nginx; no database
  container). Terraform: an application host in the existing VPC with an IAM role
  for the bucket and the database secret (D-HOST); PgBouncer listening on its
  private address with a security-group rule from that host; RDS backups on, for
  example 7 days (D-BACKUP); Terraform state in an S3 backend with locking
  (D-TFSTATE). Show `terraform plan` before any apply.
- **I5** Fix `import fitz` to `import pymupdf` in the PyMuPDF provider (C-open-4).

### Step 3 — uploads and retrieval live

- **U1** Upload and job-status routes on C5; ingestion to the private bucket with
  PyMuPDF and Tesseract; processing status visible to the client.
- **U2** Live Gemini embeddings and Pinecone (index `netra`, 1536 dimensions,
  cosine): ingest one real PDF end to end and show retrieval uses both paths.

### Step 4 — video pipeline

- **M1** TwelveLabs settings from `NETRA_TWELVE_LABS_*` into the existing
  `TwelveLabsSettings`. The index must be recreated on Marengo 3.5 + Pegasus 1.5
  (M3-PIN-1; the current one is on 2.7 / 1.2): ask Ashlin to do it in the
  dashboard, then record the exact model names.
- **M2** Private media links for TwelveLabs (M3-MEDIA-URL) via presigned S3 URLs.
- **M3** YouTube through Tunelio (M3-YT-ANALYSIS): a job that calls `/info` and
  `/create`, streams the video into the private bucket, then indexes it from a
  presigned link. Off when `NETRA_TUNELIO_API_KEY` is unset; record credits used;
  classify failures; keep the documented risks visible.
- **M4** Worker composition of the media jobs (figures, tables, equations, video
  indexing, Pegasus description windows, Marengo search) — record the M3-EXT-PROC
  decision in integration status; Arun reviews. Hand Arshad the evidence service
  for the Coordinator tools.
- **M5** Neo4j for Arshad's projection: a disposable `neo4j:5.26.30` container or
  a free Aura instance (D-NEO4J).

### Step 5 — Modal judge

- **J1** In an isolated evaluation environment, resolve the 7 pending pins in
  `evaluation/deploy/prometheus_modal.py` (D-MODAL-PINS); serve `GET /result` from
  a CPU function so a lookup never wakes the A100 (OPT-7); count cold starts in the
  run allowance (OPT-8). Deploy with a spend limit (with a yes), verify
  unauthenticated calls are rejected before the GPU starts, stop the app after
  each batch.

### Step 6 — deploy and operate

- **D1** Deploy API and worker to the application host with TLS; migrations on
  deploy; a backup and restore test; a short runbook in `infrastructure/aws/`.
- **D2** Bring `docs/architecture/runtime-baseline.md` ("Execution policy and
  deployment alignment"), `infrastructure/aws/README.md` and
  `infrastructure/compose/README.md` in line with the RDS decision.
- **E1** With Arshad and Arun, run the integration checklist on the deployed build.

## After each item, report

Item id; branch and commit; files changed; exact commands and results; what you
broke to prove the tests work; what ran on mocks, real local services, AWS or
live; decisions or blockers and who owns them; `git diff --stat`. Update the
matching row in `docs/team/integration-status.md` and your handoff note. Then
continue with the next unblocked item unless Ashlin says stop. Never go past a step
that needs Ashlin's yes without asking.
