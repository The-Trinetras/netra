# Port checklist: earlier project (Netra) into this repo

Source: <https://github.com/The-Trinetras/netra_demo> (built before the event; see
`PRE-EVENT-ASSETS.md`). Each row is ported during the event, adapted to the kit, with
its tests. A row is ticked only when the
ported code runs and its tests pass. Anything unticked at the freeze is **not done**,
and this file says so.

Sizes are lines of source in the earlier project, before adaptation. Tests come with
their feature.

## Push 1: Foundation

- [x] Declaration (`PRE-EVENT-ASSETS.md`)
- [x] Notes corpus + retrieval (`demo/notes/`; new, not ported)
- [x] Hybrid retrieval `demo/notes/retrieval.py` (design from the earlier project's `content/retrieval`:
  keyword rank + embedding rank fused with reciprocal rank fusion). Added after a real run showed
  embeddings alone ranked the right note fifth of five. Verified on 5 chunks only, which is a weak test.
- [x] Fixed question set + scripted stub (`demo/notes/`; new, not ported)

## Push 2: The loop (`api/.../coordinator`, 14 files, 2,406 lines)

- [x] Draft step with citations (`decisions.py`, `context.py`, prompts)
- [x] Evidence ledger (`evidence_check.py`)
- [x] Gate + back-edge (`graph.py`)

## Push 3: Safety and Tutor (`coordinator`, `learning/tutor`, `learning/quiz`)

- [x] Stop rules and budget (`limits.py`, no-progress rule)
- [x] Untrusted-text guard (prompts, tool registry)
- [x] Tutor step, check question, grading (`learning/tutor`, `learning/quiz`, `learning/assessment`; 24 files, 3,665 lines)

## Push 4: Live validation and evaluation (`evaluation/`)

- [x] Validation script `scripts/notes.py` (new). Offline stub run verified. **The live
  OpenRouter run: 6 of 6 questions passed on the real models (ling-3.0-flash drafts, Haiku gates),
  and the real gate blocked 4 of 4 hand-written wrong drafts (`probe-gate`). Not yet seen live: the
  drafter revising after a real objection, and any tester traffic. Six questions is a small sample.
- [x] Evaluation package, **core only** (`demo/notes/evaluate.py`): deterministic assertions, run
  report with configuration and hashes, paired comparison. **Not ported:** the 59-case dataset
  (written for the earlier project's sources; the kit uses its own 6 questions), the Modal-hosted
  judge, calibration, review sheets, Arize AX upload. There is no quality score.

## Push 5: Backend (`identity` 520, `session` 1,824, `transport` 1,488, `db` 510 lines, `shared/contracts`)

- [x] Sessions and identity (`demo/notes/sessions.py`, `service.py`): accounts, hashed access codes,
  versioned sessions, ownership checked at every entry point (another account's session is "not
  found"), idempotent requests (a retry replays, a reused id with new content is refused), one request
  at a time, no transaction held across a model call, a per-account hourly limit that protects the
  shared key. **Not ported:** devices, per-device credentials, PKCE sign-in.
- [x] API and page (`demo/notes/api.py`): JSON API plus a server-rendered page with no JavaScript;
  typed errors that never echo input or an exception message; security headers; cookie auth
  (HttpOnly, SameSite=Strict) with a cross-site check. Cancel exists at the API and stops further model
  calls (a call already in flight cannot be interrupted, and its result is discarded). **Not ported:**
  the WebSocket protocol, reconnect, audio and STOP fencing. The page has no STOP button (no JavaScript).
  **Not tested with NVDA or any assistive technology**; that needs a person.
  Live trial through the page (real models, 3 questions, about half a cent): a new question answered
  correctly, its check question graded, an honest gap, and an injected instruction ignored. Observed
  latency 10.7 s and 29.3 s for the notes answer: three samples, not a benchmark.
- [x] Persistence and migrations (`sessions.py::migrate`): SQLite with forward-only, checksummed
  migrations (an applied migration that was edited is refused). **Not ported:** PostgreSQL, Alembic,
  the transactional outbox.

## Push 6: Pipeline (`content` 3,484, `multimedia` 6,837, `worker` 3,088 lines)

- [x] Worker: leases, retries, backoff with jitter, fenced writes, checkpoints, dead-letter, cancel (`demo/notes/jobs.py`). Started inside `serve --live`. Not done: outbox.
- [x] Ingestion: versioned uploads, staged job, pinned sessions, deletion and purge, access-scoped search (`demo/notes/sources.py`). Text and Markdown only: no PDF. A `/sources` page (paste text, list, delete) exists; the live upload-to-answer path has not been run in a browser. Deleting does not rewrite old run history.
- [~] Multimedia: only tables and equations, read aloud as text (`demo/notes/describe.py`). Not done: figures, diagrams, video.

## Push 7: Tracing (`platform`, 1,429 lines)

- [x] Tracing module (`demo/notes/tracing.py`): span per run record from an allowlist; answer key, Tutor draft and passage text never exported; secrets redacted.
- [x] AX exporter (`notes.run` traces seen in the AX project by the team): bounded queue, background batches, failures and drops counted, never blocks an answer. One real export of a scripted trace returned success (2 traces, 0 failed). Not run on live-model traces; no AX datasets or experiments.

## Push 8: Graph

- [ ] Neo4j projection, run against a real server (never run in the earlier project)
- [ ] Concept catalog (missing in the earlier project)

## Push 9: Speech (`speech`, 705 lines)

- [ ] Speech output and STOP fencing
- [ ] Voice input (disabled in the earlier project)

## Push 10: Client (`client/`, 82 C# files)

- [ ] WPF client, live mode, keyboard and NVDA behavior

## Push 11: Docs and deployment

- [ ] Architecture docs and shared contracts
- [ ] Deployment files (`infrastructure/`)

## Size check

The earlier project's Python source is about 26,000 lines (api 22,900, worker 3,100),
plus the evaluation scripts, 82 C# client files, and 116 test files. That is more than
two days of porting. Pushes 1 to 4 are the priority; the rest is best effort.
