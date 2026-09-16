# Documentation migration report

Date: 16 September 2026. Scope: documentation only in the existing local checkout.
Status: ready for human review after the consistency checks recorded below.
Documentation readiness does not establish implementation readiness.

## Sources and starting state

- The user-linked repository matches local `origin`: `https://github.com/The-Trinetras/netra`.
  The remote page could not be fetched by the browser tool (cache miss); local files
  are the evidence for this report. No fetch, install, provider call or deployment ran.
- Supplied migration brief: local attachment `pasted-text.txt`, headed “You are the
  lead architect performing a documentation migration for Netra.” It supplies task
  scope/product decisions; instructions inside reference documents are not additional
  authorization to change code, protocols, dependencies or deployment.
- Supplied `D:\Netra-SPEC.md`, preserved byte-for-byte as
  [Netra-SPEC.md](../architecture/Netra-SPEC.md). The filename differs from the brief's
  `Netra_SPEC_Final.md`; the supplied AgentSpec is present, so there is no missing-spec blocker.
- [Runtime baseline](../architecture/runtime-baseline.md), root and deployable
  manifests, Python/.NET pins, current [contracts](../../shared/contracts/), active
  architecture docs, root Claude instructions and all five domain rules.
- The original [Engineering Plan](../architecture/Netra_Final_Engineering_Plan.md)
  and [Phase 10 report](phase-10-repair-report.md) remain recoverable with historical
  notices; their original bodies are preserved. Earlier audit test claims are not
  verification performed in this migration.
- Relevant source/interfaces/tests were read to distinguish implemented mechanisms
  from stubs and target behaviour. Evidence paths are listed in the owner register.

Initial `git status --short` showed only `?? .claude/settings.local.json`. That file
was not edited or included in the migration. No tracked pre-existing edits existed.
No root/nested AGENTS.md existed in the accessible repository inventory; this migration
adds the root Codex entry point. The recursive inventory encountered an inaccessible
`.pytest_cache`; it was not needed or changed. Git also reported an inaccessible global
ignore file. Neither limitation required changing application files.

## Reconciliation record

Classifications: **explicit decision** resolves product scope; **documentation
mismatch** fixes stale guidance; **implementation/contract gap** needs engineering;
**human decision** remains genuinely unresolved.

| Old statement → current decision | Classification | Affected files / action |
|---|---|---|
| Automatic status labels, mastery thresholds and review intervals → optional checks and factual activity/answer/reasoning/feedback/assistance history | Explicit decision | Root instructions, learning rule, architecture, M4 guide and checklist updated. Historical plan preserved; no invented thresholds or code deletion. |
| General web/Drive, braille and hands-free voice requirements → deferred; YouTube discovery remains | Explicit decision | Scope, agent boundaries, all relevant rules and M2/M3/M5 guides updated; provider choices retained. |
| Broad specialist/assessment scope → remove browser extensions/screenshots, sonification, specialized code navigation and assessment-platform expansion | Explicit decision | Canonical scope and workstream guidance narrowed without changing code or schemas. |
| Ordinary retrieve-and-answer framing → inspect evidence, detect a gap, change strategy, validate, answer and adapt | Explicit decision | Scope, Coordinator rules/boundaries, message flow and acceptance checklist; sufficient first evidence needs no extra retrieval. |
| Missing/malformed PDF authority link and blank README/team files → actual supplied spec plus a domain-specific authority map | Documentation mismatch | CLAUDE.md, new AGENTS.md, READMEs, current scope, ownership and five guides. |
| Snapshot still called undefined → approved reference-only snapshot exists; wiring is incomplete | Documentation mismatch | Message-flow playback-ack section corrected against committed schemas. Historical audit marked as superseded evidence. |
| “Studied — understanding not tested” could be read as a new status → factual activity wording only | Explicit decision + implementation/contract gap | Scope/history docs and shared README; v1 enums unchanged, factual representation assigned to M1/M4/M2. |
| Existing 4/6/20 limits versus AgentSpec 8/12/45 and two revisions → retain existing limits, mark proposals | Human decision | Runtime, scope, rules and M1/M4 guides; no configuration or constants changed. |
| Committed locks described as present → locks absent in checkout | Documentation mismatch + implementation gap | Runtime/README explain missing Python/NuGet locks and unverified installation; no resolution attempted. |
| Playback/URL access implies analysis → separate permission/technical readiness gates | Explicit decision + implementation gap | Scope, M3/M5 rules/guides and checklist; web-view dependency remains proposed. |
| Historical Compose PostgreSQL versus AgentSpec RDS/PgBouncer → preserve EC2/Compose API-worker boundary and flag placement/pooling | Human decision | Runtime, overview, deployment README and M2 guide; no deployment migration. |
| Historical passing audit implies full readiness → distinguish target, current source and checks actually run | Documentation mismatch | Audit notice, README, all guides and this report; no integration/accessibility/provider success claim. |

## Files changed

18 existing documents updated:

- [CLAUDE.md](../../CLAUDE.md), [root README](../../README.md),
  [shared README](../../shared/README.md), [AWS README](../../infrastructure/aws/README.md).
- All five domain rules: [coordinator](../../.claude/rules/coordinator.md),
  [backend-data](../../.claude/rules/backend-data.md), [multimedia](../../.claude/rules/multimedia.md),
  [learning](../../.claude/rules/learning.md), [client](../../.claude/rules/client.md).
- Architecture: [overview](../architecture/overview.md),
  [agent boundaries](../architecture/agent-boundaries.md), [data ownership](../architecture/data-ownership.md),
  [message flow](../architecture/message-flow.md), [runtime baseline](../architecture/runtime-baseline.md),
  and a notice on the [original plan](../architecture/Netra_Final_Engineering_Plan.md).
- [Historical audit notice](phase-10-repair-report.md),
  [ownership](../team/ownership.md), [integration checklist](../team/integration-checklist.md).

9 new documents:

- [AGENTS.md](../../AGENTS.md), [current scope](../architecture/current-scope.md),
  [supplied AgentSpec copy](../architecture/Netra-SPEC.md), this report.
- [M1](../team/M1.md), [M2](../team/M2.md), [M3](../team/M3.md),
  [M4](../team/M4.md), [M5](../team/M5.md) implementation guides.

No application code, runtime prompt Markdown, tests, wire schemas, migrations,
runtime/dependency/deployment files or existing user changes were modified.

## Implementation and contract gaps by owner

These findings come from source inspection, not execution. They are integration
tasks; only the decision table below requires choices before dependent work.

| Owner | Current evidence | Required integration |
|---|---|---|
| M1 | [Coordinator graph](../../api/src/netra_api/coordinator/graph.py) raises `NotImplementedError`; [endpoint](../../api/src/netra_api/transport/websocket/endpoint.py), [dispatcher](../../api/src/netra_api/transport/websocket/dispatcher.py) and [config](../../api/src/netra_api/config.py) are empty. [Limits](../../api/src/netra_api/coordinator/limits.py) define 4/6/20; [session repository](../../api/src/netra_api/session/repository.py) is a protocol. | Implement bounded evidence-gap repair, scoped context/compaction, service wiring and durable session recovery; prove shared accounting and cancellation. Handoff only carries a deadline, so preserve shared counters in runtime rather than assuming the schema alone prevents budget reset. |
| M2 | [Source models](../../api/src/netra_api/content/sources/models.py), [reading blocks](../../api/src/netra_api/content/reading/blocks.py) and [retrieval protocol](../../api/src/netra_api/content/retrieval/service.py) exist; [migration versions](../../api/migrations/versions/) only has a placeholder. [Job schema](../../shared/contracts/jobs/v1/job.schema.json) and [Compose](../../infrastructure/compose/docker-compose.yml) are empty; no Python/NuGet lockfiles exist. | Concrete persistence, ingestion, authorized retrieval, reading/table mappings, job/outbox recovery and projections. Coordinate M4 history storage and M1 infrastructure decisions. Lock/install/backup/live database validation remains future authorized work. |
| M3 | [Observation types](../../api/src/netra_api/multimedia/evidence.py), [video models](../../api/src/netra_api/multimedia/video/models.py), [timestamps](../../api/src/netra_api/multimedia/video/timestamps.py) and [video service protocol](../../api/src/netra_api/multimedia/video/service.py) exist. | Original-media fidelity, PDF/table/graph/equation extraction, permitted video processing and separate analysis readiness; M2 persistence and M5 actual player-time integration. Adapter interfaces/tests do not establish live provider access. |
| M4 | [Tutor loop](../../api/src/netra_api/learning/tutor/agent.py) and [Learning proposal commit](../../api/src/netra_api/learning/assessment/service.py) are stubs. [Assessment models](../../api/src/netra_api/learning/assessment/models.py) retain status types and limited answer/outcome/hint records; [review policy](../../api/src/netra_api/learning/review/policy.py) remains. [Evaluation interfaces](../../evaluation/scripts/interfaces.py) are not integrated evaluators. | Implement observed-response adaptation, factual delivered activity/reasoning/feedback/assistance, optional-check validation/commits and relevant history selection. Coordinate legacy contract migration; preserve Neo4j authority boundaries. Integrate source-checked evaluation before secondary model judges. |
| M5 | [MainWindow](../../client/src/Netra.Desktop/Views/MainWindow.xaml) is a transcript/control shell; [client project](../../client/src/Netra.Desktop/Netra.Desktop.csproj) declares no web-view package. [Connection manager](../../client/src/Netra.Desktop/Networking/ConnectionManager.cs) and [interruption controller](../../client/src/Netra.Desktop/Audio/InterruptionController.cs) contain mechanisms, not an accepted complete journey. | Accessible library/source selection, study views, activation/PTT, video controls and actual time, server integration, NVDA/focus/STOP/reconnect tests. Review player dependency and missing shared payloads with M1/M3. |

Shared contract gap: [Coordinator → Tutor](../../shared/contracts/agent/v1/coordinator_to_tutor.schema.json)
requires `assessment_summaries`; its item statuses remain `not_assessed`, `needs_review`,
`developing`, `demonstrated_recently`. [Tutor → Coordinator](../../shared/contracts/agent/v1/tutor_to_coordinator.schema.json)
retains `review_requested`. No schema was changed. An empty summary list is structurally
allowed but is not a substitute for representing relevant factual history. Do not
fabricate status values, conflate absence/unavailability or introduce new wire enums.

## Genuine unresolved decisions

| Decision | Owners / affected work |
|---|---|
| Adopt or reject proposed 8/12/45 limits, nested model accounting and two-substantive-revision semantics | M1 with M3/M4; bounded execution validation. Existing 4/6/20 stays in force. |
| Versioned factual-history representation and treatment of legacy status/review handoff fields | M1/M4 with M2/M5; teaching history, commits and public presentation. |
| Sufficient evidence/validation criteria for optional questions and feedback; unapproved retention values | M4/product with M2; grounded checks and lifecycle. Removed mastery thresholds/review intervals are not pending. |
| Desktop web-view dependency and any missing player-time/control representation | M5/M3 with M1; YouTube playback and evidence integration. |
| RDS/PgBouncer target versus historical Compose database placement | M1/M2; deployment placement and connection pooling. No new infrastructure is installed. |
| Need/shape of an external job contract; neutral Python/C# mirror-package location | M2/M1 for jobs, M1/M5 for mirrors; existing approved schemas remain authoritative. |
| Total binary-message size bound | M1/M5; transport hardening. Existing 16 KiB header bound and framing remain unchanged. |

Provider availability, missing wiring and unexecuted usability tests are verification
or implementation gaps, not excuses to silently choose new providers or product policy.
Independent work against approved boundaries can proceed while decisions are pending.

## Separate consistency review and checks

After writing the documents, reread the active architecture, root/path instructions
and M1–M5 guides as onboarding material. Check scope, ownership, proposal status,
wire vocabulary, evidence claims and internal references independently of drafting.
Fixes from this pass include the stale snapshot note, historical learning-tool names
being read as current permissions, and present-tense flows that needed a target label.

Verification commands and their results are recorded below. Application/provider/deployment/accessibility tests are not part of
this documentation-only verification. No packages were installed; no secrets read;
no files staged, committed, pushed, reset, restored or discarded.

Run from the repository root in PowerShell:

```powershell
git diff --check
git diff --stat
git status --short
$report = Get-Content -LiteralPath 'docs/audits/documentation-migration-report.md' -Raw
$validation = [regex]::Match($report, '(?s)```javascript\r?\n(.*?)\r?\n```').Groups[1].Value
$validation | node
```

The following Node standard-library check validates local Markdown link targets,
explicit repository paths, documentation-only scope, the supplied-spec copy,
historical bodies and current budget/schema facts. It does not test application
behaviour or external URLs. Historical document bodies are intentionally excluded
from active-link validation; their new notices are included. Runtime prompt Markdown
is excluded from documentation scanning and explicitly checked for no changes.

```javascript
const fs = require('fs');
const path = require('path');
const cp = require('child_process');
const assert = require('assert/strict');
const git = (...args) => cp.execFileSync('git', args, {encoding: 'utf8'});
const lines = s => s.trim().split(/\r?\n/).filter(Boolean);
const read = p => fs.readFileSync(p, 'utf8').replace(/\r\n/g, '\n');
const tracked = lines(git('ls-files'));
const untracked = lines(git('ls-files', '--others', '--exclude-standard'));
const changed = lines(git('diff', '--name-only'));
assert(changed.every(p => p.endsWith('.md') && !p.startsWith('api/')));
assert(untracked.every(p => p.endsWith('.md') || p === '.claude/settings.local.json'));
assert.equal(git('diff', '--cached', '--name-only').trim(), '');
assert(fs.readFileSync('D:/Netra-SPEC.md').equals(
  fs.readFileSync('docs/architecture/Netra-SPEC.md')));
const historical = new Map([
  ['docs/architecture/Netra_Final_Engineering_Plan.md', '# Netra engineering plan'],
  ['docs/audits/phase-10-repair-report.md', '# Phase 10 Final Integration Audit']
]);
for (const [p, title] of historical) {
  const current = read(p);
  const original = git('show', 'HEAD:' + p).replace(/\r\n/g, '\n');
  assert.equal(current.slice(current.indexOf(title)), original);
}
const docs = [...new Set([...tracked, ...untracked])]
  .filter(p => p.endsWith('.md') && !p.startsWith('api/'));
let links = 0, explicitPaths = 0;
const broken = [];
for (const p of docs) {
  let text = read(p);
  if (historical.has(p)) text = text.slice(0, text.indexOf(historical.get(p)));
  // Code examples are not rendered Markdown links or active path references.
  text = text.replace(/```[\s\S]*?```/g, '');
  for (const match of text.matchAll(/\[[^\]\n]+\]\(([^)\n]+)\)/g)) {
    const target = match[1].replace(/^<|>$/g, '').split('#')[0];
    if (!target || /^[a-z]+:/i.test(target)) continue;
    links++;
    if (!fs.existsSync(path.resolve(path.dirname(p), decodeURIComponent(target))))
      broken.push(p + ' -> ' + target);
  }
  for (const match of text.matchAll(/`((?:api|client|worker|shared|docs|infrastructure|\.claude)\/[^`]+)`/g)) {
    const target = match[1];
    if (/[\s{}*]/.test(target)) continue;
    explicitPaths++;
    if (!fs.existsSync(target) && !fs.existsSync(path.resolve(path.dirname(p), target)))
      broken.push(p + ' -> explicit path ' + target);
  }
}
assert.deepEqual(broken, []);
const limits = read('api/src/netra_api/coordinator/limits.py');
for (const [name, value] of Object.entries({
  MAX_MODEL_DECISIONS_PER_TURN: '4', MAX_TOOL_CALLS_PER_TURN: '6', ANSWER_DEADLINE_SECONDS: '20.0'
})) assert(limits.includes(name + ' = ' + value));
const input = JSON.parse(read('shared/contracts/agent/v1/coordinator_to_tutor.schema.json'));
assert(input.required.includes('assessment_summaries'));
assert.deepEqual(input.$defs.AssessmentSummary.properties.status.enum,
  ['not_assessed', 'needs_review', 'developing', 'demonstrated_recently']);
const output = JSON.parse(read('shared/contracts/agent/v1/tutor_to_coordinator.schema.json'));
assert(output.properties.proposed_learning_events.items.properties.event_type.enum.includes('review_requested'));
console.log(JSON.stringify({documents: docs.length, localLinks: links, explicitPaths,
  modifiedDocs: changed.length, newDocs: untracked.filter(p => p.endsWith('.md')).length,
  scope: 'documentation only', suppliedSpec: 'byte-identical', historicalBodies: 'preserved',
  budgetAndLegacySchemaFacts: 'confirmed'}, null, 2));
```

Results:

- `git diff --check`: passed; no whitespace errors. Git emitted line-ending
  normalization warnings (LF will become CRLF), not check failures.
- Node validation: passed for 27 documents, 292 local Markdown links and 4 explicit
  repository paths; 18 modified documents and 9 new documents. Supplied spec is
  byte-identical; both historical bodies match HEAD after newline normalization.
  Documentation-only scope, no staged changes, 4/6/20 constants and legacy handoff
  vocabulary were confirmed.
- Active-scope `rg` review below: the only final targeted match was the explicit
  statement that automatic learning-status derivation/review is **not** required.
  Broader review of mastery, braille, web/Drive, voice and budget mentions found
  historical, removed/deferred or compatibility/proposal descriptions, not new tasks.
- Restricted-path diff below: empty. No application, test, schema, manifest or
  deployment changes. `git status --short` retains the original untracked local
  settings file; new Markdown files remain untracked because nothing was staged.
- Header-bound source inspection confirms `MAX_HEADER_BYTES = 16 * 1024` in the
  existing Python framing implementation; this does not establish end-to-end transport.
- Application tests, dependency installation/locking, live providers, deployment,
  original-media acceptance and keyboard/NVDA participant tests were not run.
  They are outside this documentation migration. Historical test counts were not reused.

Additional exact inspection commands used in the final review:

```powershell
git diff --numstat
rg -n 'derive review|derive learning|Only these initial labels|learning.status derivation|Coordinate math/braille|Local voice.activity interruption|Snapshot representation:.*Pending|approved numeric learning.policy' CLAUDE.md AGENTS.md .claude/rules docs/team docs/architecture/overview.md docs/architecture/agent-boundaries.md docs/architecture/data-ownership.md docs/architecture/message-flow.md
rg -n '16 \* 1024|16384|16_384|MAX_HEADER' api/src/netra_api/transport/audio/frame.py client/src/Netra.Desktop/Audio/AudioFrameHeader.cs
git diff --name-only -- api worker client shared/contracts infrastructure/compose infrastructure/docker .github pyproject.toml .python-version
```

Final `git diff --stat` (tracked files only):

```text
 .claude/rules/backend-data.md                     |  15 +-
 .claude/rules/client.md                           |  27 ++-
 .claude/rules/coordinator.md                      |  14 ++
 .claude/rules/learning.md                         |  46 ++--
 .claude/rules/multimedia.md                       |  13 +-
 CLAUDE.md                                         | 251 ++++++++--------------
 README.md                                         |  32 +++
 docs/architecture/Netra_Final_Engineering_Plan.md |   9 +
 docs/architecture/agent-boundaries.md             |  31 ++-
 docs/architecture/data-ownership.md               |  11 +-
 docs/architecture/message-flow.md                 |  40 +++-
 docs/architecture/overview.md                     |  26 ++-
 docs/architecture/runtime-baseline.md             |  31 ++-
 docs/audits/phase-10-repair-report.md             |   9 +
 docs/team/integration-checklist.md                |  51 +++++
 docs/team/ownership.md                            |  47 ++++
 infrastructure/aws/README.md                      |  17 ++
 shared/README.md                                  |  28 +++
 18 files changed, 464 insertions(+), 234 deletions(-)
```

This stat includes no pre-existing changes and excludes the 9 new Markdown files
and the pre-existing untracked `.claude/settings.local.json`. The complete migration
contains 27 documentation files, as listed above. The baseline is ready for human
review; unresolved engineering and decision gates remain explicitly assigned.
