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
- [ ] M4: run deterministic/source checks before secondary Ragas/Prometheus-2 evaluation;
  record rubric/test set/model configuration, failures and human disagreements.
  Judges stay outside the student's deadline and do not become product agents.

Use labelled fixed responses first, then separately authorized live integrations.
Do not install dependencies or contact providers just to complete this checklist.
Report missing gates honestly; PDF-only remains incomplete against the video target.
Documentation check evidence appears in the [migration report](../audits/documentation-migration-report.md).
