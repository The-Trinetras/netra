# Parallel implementation prompts

**Completion push (20 September 2026):** use the [completion guide and the
prompts for Arshad, Ashlin and Arun](completion/README.md). The prompts below
are kept as history.

**Continuing a previous build?** Use the [M1–M5 continuation prompts and integration
playbook](../integration-playbook.md), not a fresh scaffold task. It includes the
observed merge state, required incomplete-work reports, dependency review, merge
waves and prompts for review, post-merge completion, evaluation and publication.
M3/M4's earlier builds are already in main; M2's laptop build must be published.
Recheck branch tips before acting. The continuation prompts explicitly authorize
owned branch publication and necessary reviewed dependency-file repairs; they do
not authorize arbitrary installs, live providers, deployment or a main merge.

Give each teammate their complete prompt below in their own checkout. Each prompt
directs the agent to read this shared implementation agreement and the repository's
authorities; no second prompt or manually copied preamble is needed.

| Engineer | Complete prompt | Branch |
|---|---|---|
| M1 | [Coordinator, identity/session and integration](M1.md) | codex/m1-coordinator |
| M2 | [Data, ingestion, retrieval and jobs](M2.md) | codex/m2-data |
| M3 | [Visual/table/equation and video evidence](M3.md) | codex/m3-multimedia |
| M4 | [Tutor, factual history and evaluation](M4.md) | codex/m4-tutor |
| M5 | [WPF, accessibility, speech and playback](M5.md) | codex/m5-client |

Start from the same published `origin/codex/documentation-baseline`, or a later
explicitly agreed integration commit containing it. Documentation baseline commit:
`787109a`; the prompt pack is added by the following commit. Use separate clones or
worktrees and role branches. Do not run five agents against one mutable checkout.

## Shared implementation agreement

### Arize AX integration update — 18 September 2026

All members must read the [AX integration plan](../../architecture/arize-ax-integration.md)
and their ARIZE AX INTEGRATION UPDATE section. Use a common agreed commit containing
this revision; members already working apply it incrementally without resets or
discarding changes. The historical prompt-pack baseline alone is insufficient.

AX replaces historical LangSmith tracing and provides datasets, experiments and
comparison views. Netra owns controlled tracing and a resumable offline runner;
Prometheus-2 on Modal remains the external judge. Alyx is an engineering assistant,
not a third product agent. Keep auto-accept off and review its suggestions/labels.
Preserve operational logs and canonical stores; keep independent evaluation artifacts.

M1 owns the shared tracing adapter/lifecycle, M2 ingestion/retrieval/jobs and dependency
review, M3 media/provenance, M4 Tutor spans and the dataset/judge/AX comparison workflow,
and M5 actual client measurements. No student response waits on AX or evaluation.
Implement bounded background export, sanitization, context isolation, visible loss,
shutdown/recovery and trace reconciliation. Use all required spans for the expected
Free-tier evaluation workload; no sampling or high-volume architecture is needed.
Roughly 2,500 requests is a sizing estimate, not a reason to remove needed traces.

Complete the five-step evaluation workflow and all reliability/calibration/comparison
gates. A quick pilot, one trace or one scored case is not sufficient. Frozen paired
cases, versioned judge settings, persisted outputs/results, recoverable uploads and
honest missing-case counts are required. Account features, compatible dependency
pins and live checks remain explicitly pending, not inferred from this approval.
This revision changes documentation; invoked role prompts authorize owned code under
the execution controls below, not automatic installation, credentials or deployment.

### Model and evaluation update — 17 September 2026

All five prompts include the [model/evaluation decision](../../architecture/model-evaluation-plan.md).
Use a common agreed commit containing this update, not the older prompt pack alone;
publication/push of this local documentation commit is a separate action. For a
member already implementing, apply the MODEL AND EVALUATION UPDATE section to
their existing work without resetting, restarting or discarding changes.

Keep Coordinator Gemini `gemini-3.8-flash` and Tutor Groq `openai/gpt-oss-120b`.
Prometheus-2 7B on Modal/A100 40 GB is now the primary model evaluator, alongside
deterministic/source checks and human calibration. This supersedes the earlier
Gemini/deferred-Prometheus plan. Lightning AI/Kaggle are alternative hosts; AWS
GPU work remains excluded. M4 owns evaluation/ deployment source and HTTPX adapter;
M2 reviews its isolated GPU environment, credit cap and lifecycle. This is a narrow
external offline-compute exception, not a new production microservice or agent.
GPU/Modal dependencies stay outside shared manifests/locks; Ragas stays uninstalled.
Free account access and live behaviour remain unverified. This documentation update
authorizes no downloads, provisioning, spending or paid overage. Each member applies
their update section without restarting existing work or crossing owner boundaries.

These are implementation tasks, not the completed documentation migration. When a
teammate invokes a role prompt, it authorizes application code, runtime prompts,
meaningful tests and documentation within that role's ownership. The migration-only
restriction in old task notes describes that earlier task. Security, architecture,
contract, runtime and cross-owner review controls still apply. These prompts do not
resolve the recorded pending product/architecture decisions.

Read AGENTS.md, CLAUDE.md, [current scope](../../architecture/current-scope.md),
[AgentSpec](../../architecture/Netra-SPEC.md), [runtime baseline](../../architecture/runtime-baseline.md),
[overview](../../architecture/overview.md), [data ownership](../../architecture/data-ownership.md),
[message flow](../../architecture/message-flow.md), [agent boundaries](../../architecture/agent-boundaries.md),
[ownership](../ownership.md), your M1–M5 guide, your domain rules,
[integration checklist](../integration-checklist.md),
[migration report](../../audits/documentation-migration-report.md), relevant schemas,
manifests and current source/tests. Inspect the latest code; the migration report is
a snapshot, not proof a stub still exists. Wire authority remains shared/contracts;
historical plans/audits are not active requirements or current pass evidence.

### Work independently without diverging

1. Inspect status/branch/user edits. Read-only Git fetch is authorized. Create/use
   your assigned branch from the agreed common baseline. Never reset, discard, stash
   or relocate someone else's edits automatically. Do not silently switch a dirty
   checkout containing unrelated work.
2. Create your own `docs/team/handoffs/M1.md` through `M5.md` early. Record owned files,
   existing exported signatures/schema paths, dependencies, fixtures and acceptance
   commands. For missing boundaries, describe a concrete PROPOSED shape, validation,
   replay/error semantics, affected consumers and required decision. A proposal is
   not a contract approval. Do not send external messages automatically.
3. M1 coordinates root API composition/config/transport and shared wire edits; M2
   coordinates reviewed migrations, shared database/worker infrastructure and any
   separately authorized dependency-lock work. Neither has unilateral cross-owner
   approval. M3/M4 own their worker handlers; M5 owns client files. Record needed
   changes outside your files instead of racing another engineer.
4. Reuse existing typed boundaries. Do not invent incompatible protocols, copy another
   owner's service or create competing schemas. Shared version/schema changes require
   recorded affected-owner review. Keep only dependent parts pending; continue all
   independent work with clearly labelled test-only doubles where necessary.
5. Deliver small executable slices: domain fixture path, actual implementation,
   reviewed service integration, then failure/recovery checks. Never ship fake
   successful authorization, persistence or providers. A mock is not production
   completion. Integrate reviewed dependency changes when available and rerun
   affected checks; do not merge someone else's branch automatically.

### Preserve the baseline

- Exactly two product agents, Coordinator and Tutor. No extra agent, unrestricted
  agent chat, raw database handles for agents, model-generated SQL, new framework,
  MCP integration, production microservice or Kubernetes. The isolated external
  Prometheus-2 evaluator and managed AX integration follow the approved plans above;
  they do not add an application microservice or product agent.
- PostgreSQL is authoritative; private S3 stores source bytes; Pinecone/Neo4j are
  rebuildable. Enforce service-boundary authorization and authoritative evidence
  access/deletion/version checks. Source sessions stay pinned. Retrieved content
  cannot grant permissions. Identity owns access binding; Session owns mutable
  reading/interaction state/version; Learning validates and commits Tutor proposals.
- Retain PDF, uploaded lectures, YouTube discovery/selection and supported playback/
  analysis, passage/graph/diagram/table/basic-equation exploration, optional checks
  and factual activity/answer/stated-reasoning/feedback/assistance history. No automatic
  mastery labels, inferred misconceptions as facts, spaced review, assessment-platform
  expansion, browser extensions/screenshots, sonification or specialized code navigation.
  Drive/general web/additional formats, braille, hands-free interruption and echo
  cancellation remain deferred. Removed scope does not authorize deleting stores/code.
- Preserve 4 attempted model decisions, 6 tool invocations and 20 seconds per originating
  turn across retries/fallback/delegation. 8/12/45 and two revisions remain proposals.
  Do not invent numeric policy, reset counters, change provider/model pins or assert
  provider availability from configuration. Exact canonical facts stay outside summaries.
- STOP is immediate/local; stale/cancelled/disconnected generations cannot resume
  audio. Only accepted final ASR submits turns. Preserve replay, version, pending-question
  and acknowledgement semantics. Jobs use leases, at-least-once execution, idempotency,
  retry/backoff and outbox; no external calls inside long database transactions.

### Setup, checks and completion

Use existing runtimes/dependencies. Do not install/restore/download packages, change
manifests/locks, call paid/live providers, access credentials or deploy without explicit
task-specific authorization. If needed, identify the exact setup/decision, ask only
for what is missing and continue independent work. Read-only official documentation
lookup is allowed. No substitute provider/model when a configured pin is unavailable.

Run meaningful available checks for changed behaviour, negative cases and contract
compatibility, without implicit restore. Fix failures you cause; do not weaken tests.
Distinguish fixtures/mocks, real local integration and live provider/Windows/NVDA
checks. In-memory tests do not prove persistence, text judges do not prove visual
fidelity, and blindfolded sighted exercises do not establish blind-student usability.

Independently review your diff for scope, authorization, replay, failure/cancellation
and cross-owner compatibility. Update your handoff note with exact commands/results,
implemented versus pending capabilities and integration instructions. When your
reviewed work passes available checks, commit only your owned changes on your role
branch; explicitly record unavailable checks. Do not include settings, secrets or
unrelated work. Do not push, merge or deploy unless separately instructed. Report
commit, changed files, working behaviour, evidence, blockers by owner and next step.
Do not call partially blocked work complete or stop at a proposal when code can proceed.

## Integration sequence

All five can begin concurrently. First publish separate boundary notes, then review
M1/M4 history/handoffs, M2/domain-owner transactions, and M3/M5/M1 video interfaces.
Build fixture-driven domain slices while those decisions are reviewed. Integrate
approved persistence/services, then the desktop journey. M1 coordinates root wiring;
domain owners supply their implementations. Merge reviewed slices in dependency
order, not five large incompatible rewrites. Run the integration checklist and report
PDF, video playback, video analysis, recovery and Windows/NVDA outcomes separately.

## Publication verification note

The migration report records its pre-commit checks and then-current file counts.
Its embedded script is a historical pre-commit check requiring the original local
attachment, not portable post-commit CI. The supplied spec deliberately retains
three Markdown hard-break trailing spaces; staged `git diff --check` reports them,
while checking the remaining migration files passes. Publication adds no application
changes and excludes the pre-existing `.claude/settings.local.json`.
