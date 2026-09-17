# Parallel implementation prompts

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

### Model and evaluation update — 17 September 2026

All five prompts include the [model/evaluation decision](../../architecture/model-evaluation-plan.md).
Use a common agreed commit containing this update, not the older prompt pack alone;
publication/push of this local documentation commit is a separate action. For a
member already implementing, apply the MODEL AND EVALUATION UPDATE section to
their existing work without resetting, restarting or discarding changes.

Keep Coordinator Gemini `gemini-3.8-flash` and Tutor Groq `openai/gpt-oss-120b`.
Required evaluation uses deterministic/source/human checks. Optional offline
Gemini rubric scoring is the selected new evaluation plan, not a silent live-model
replacement. Prometheus-2/AWS GPU work is deferred; do not install Ragas. Existing
SDK pins suffice for the planned evaluator. Free account access, quotas and live
behaviour remain unverified; no paid fallback, downloads or provisioning are
authorized by this documentation update. Each role owns the changes listed in its
prompt and coordinates cross-owner boundaries as before.

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
  MCP integration, microservice or Kubernetes.
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
