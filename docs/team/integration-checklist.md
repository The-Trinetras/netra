# Integration acceptance checklist

These are approved target checks, **not executed or passing results**. Use
[current scope](../architecture/current-scope.md), [ownership](ownership.md),
[message flow](../architecture/message-flow.md) and the
[AgentSpec acceptance fixture](../architecture/Netra-SPEC.md). Record date, commit,
runtime, fixture/source versions, actual result, limitations and owner for every run.

- [ ] M1/M2/M5: accessible selection → authorized source → saved pinned position.
  Cancelled selection uploads nothing; unauthorized IDs fail closed.
- [ ] M1/M2/M3: use the Ohm's Law fixture with current on x, voltage on y,
  table rows (1 A, 2 V), (2 A, 4 V), (3 A, 6 V), and `V = I × R`.
  Missing transcript axes must cause a recorded evidence gap and changed retrieval
  action; accept an explanation only after checking graph evidence against the source.
- [ ] M1/M3/M4: sufficient first evidence answers directly; unreadable evidence stays
  a stated gap and ends with useful clarification or bounded stop, not invented detail.
- [ ] M2/M3: exact source/version IDs, quotations, table headers/values, equation signs,
  units and graph relationships match original media. A valid citation alone does not
  prove semantic support; reject unauthorized, deleted or incompatible references.
- [ ] M3/M5: uploaded lecture and YouTube search/selection retain exact selected identity
  and actual player time. Check playback access, focus and controls separately from
  analysis permission, ingestibility and visual/audio evidence. Include rejected media.
- [ ] M1/M4/M2: Tutor receives scoped typed context and responds to stated reasoning;
  preserve delivered material, answers, feedback and assistance separately. A declined
  check records untested study, not a grade or new mastery enum. Protect answer keys.
- [ ] M1/M4/M5: wait without model calls; compact dialogue without losing canonical
  facts; reconnect to the same pending question and exact source/reading position.
- [ ] M1/M2/M5: identical request replay has one effect; conflicting reuse fails;
  version conflicts do not move twice. Fresh frames retain the logical request ID.
- [ ] M1/M5: STOP is immediate locally; cancellation/disconnect fences late audio;
  continue cannot resurrect it. Only actual played/acknowledged content advances position.
- [ ] M1/M3/M4: existing 4/6/20 budget is shared across retries, fallback and delegation.
  Exercise timeouts/cancellation and no-progress loops. Evaluate 8/12/45 and the
  two-revision proposal only as a labelled experiment pending policy approval.
- [ ] M2/M3/M4: lease loss, crash after an external effect, retry/backoff, duplicate
  jobs, outbox replay and stale projection events preserve canonical truth. No long
  transaction spans an external call; projections are rebuildable.
- [ ] M1–M5: injected source instructions cannot grant permissions or leak another
  student's material. Tool inputs/results and response eligibility are checked.
- [ ] M5 with M3/M4: keyboard-only and NVDA tasks cover launch, selection, table/graph
  exploration, lecture question, STOP, return and reduced speech mode without help.
  Test shortcut conflicts and focus loss. Record blind-participant feedback separately;
  blindfolded sighted testing cannot establish blind-student usability.
- [ ] M4: run deterministic/source/human checks alongside Prometheus-2 7B scoring
  and custom Ragas-style metrics under the [model/evaluation plan](../architecture/model-evaluation-plan.md).
  Record checkpoint/tokenizer/rubric/test-set/host versions and calibrated agreement,
  failures, unscored cases and human disagreements. Judges stay outside student turns.
- [ ] M1/M4: simulate exhausted credits, timeout, OOM and malformed/truncated judge
  output; preserve completed results without model substitution or student impact.
  Unscored cases leave the Prometheus evaluation milestone incomplete.
- [ ] M2/M4: verify authenticated Modal A100 40 GB inference, rejection before GPU
  allocation for unauthenticated calls, one container, scale-to-zero, credit/spend
  caps, resume after interruption, actual cost and explicit shutdown. No AWS GPU,
  Ragas or GPU dependencies in the shared runtime. Record checks as pending until run.
- [ ] M4: validate grading template, source-checked reference answers, strict 1–5
  parsing, rubric-injection resistance, held-out cases and human disagreement.
  Any Lightning/Kaggle host switch preserves provenance and rechecks configuration.

## Arize AX end-to-end acceptance

Follow the [AX plan](../architecture/arize-ax-integration.md). These gates require
complete quality/recovery evidence; one successful trace or scored case is only a
connectivity check. The expected Free-tier workload needs no scaling infrastructure.

- [ ] M1/M2: review compatible telemetry pins and one process-level provider;
  demonstrate bounded background export with no AX I/O/flush on the response path.
  Record concurrency/context isolation, error/cancellation span closure and retry
  identity. Worker links preserve job attempts without altering idempotent effects.
- [ ] M1–M4: verify allowlisted span attributes/events and safe errors exclude
  secrets, signed URLs, private student/assessment content and private reasoning.
  Show source/version provenance and distinguish real, fixed and replayed cases.
- [ ] M1/M2/M4: inject AX timeout, invalid credentials, 429, full queue and shutdown;
  student operations remain correct, loss/flush diagnostics remain visible and
  incomplete traces cannot pass acceptance. Confirm required spans in AX against
  the persisted evaluation manifest; exporter acknowledgement alone is insufficient.
- [ ] M1/M5: compare response/first-output latency and responsiveness with tracing
  enabled/disabled and a stalled exporter. Record environment/sample counts and
  actual monotonic-clock STOP-to-silence, playback acknowledgement and exact return.
- [ ] M4 with all owners: complete reviewed reference dataset, error analysis,
  versioned evaluation names, calibrated rubrics and external Prometheus execution.
  Freeze held-out case/reference/source versions; preserve original-media and real
  accessibility checks alongside scores. Review Alyx suggestions; auto-accept stays off.
- [ ] M4: confirm AX dataset/version, experiment, case/task and trace IDs map to
  durable snapshots/outputs/results. Resume after partial scoring/upload and ambiguous
  timeout without duplicate experiments or unnecessary producer/judge calls. Test
  invalid configuration/schema, duplicate resume and exhausted capacity/credits.
- [ ] M4/M1: produce confirmed baseline/candidate AX comparison using the same cases,
  references, rubric and judge settings; record intended producer changes and isolated
  state. Report per-case deltas/regressions, expected/completed/unscored counts,
  deterministic failures, human agreement and trace completeness. Partial pairs
  cannot establish improvement; rescore both outputs when judge settings change.
- [ ] M4: reproduce the comparison from retained independent artifacts even after
  AX trace expiry/unavailability. Document run/resume/recovery commands and measured
  quota use. Separate local fixtures, real integration and live AX/Modal evidence;
  no fake completion, silent paid tier or replacement judge.

Use labelled fixed responses first, then separately authorized live integrations.
Do not install dependencies or contact providers just to complete this checklist.
Report missing gates honestly; PDF-only remains incomplete against the video target.
Documentation check evidence appears in the [migration report](../audits/documentation-migration-report.md).
