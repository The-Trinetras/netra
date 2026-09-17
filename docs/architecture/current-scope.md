# Current product scope

Documentation baseline: 16 September 2026. This describes approved target
behaviour, not a claim of completed implementation or verified usability.

## Authority by domain

The supplied documentation-migration brief's explicit product decisions govern
this page. The supplied `D:\Netra-SPEC.md` is preserved as the
[AgentSpec](Netra-SPEC.md); it is the supplied spec despite the brief calling it
`Netra_SPEC_Final.md`. Its execution-budget proposals do not override runtime policy.

- **Product:** this scope page and the supplied AgentSpec, subject to the explicit
  migration decisions recorded here.
- **Protocol:** [shared/contracts](../../shared/contracts/) governs field names,
  enums, versions and typed handoffs. Product prose cannot migrate a protocol.
- **Runtime:** [runtime baseline](runtime-baseline.md) and dependency manifests;
  exact locks govern when present. Missing locks are a gap, not permission to resolve.
- **Architecture:** [overview](overview.md), [data ownership](data-ownership.md),
  [agent boundaries](agent-boundaries.md) and [message flow](message-flow.md)
  preserve ownership, security, persistence and deployment boundaries.
- **Current implementation:** source and tests establish what exists. Stubs,
  interfaces and mock success do not establish integrated behaviour.
- **History:** the [original Engineering Plan](Netra_Final_Engineering_Plan.md)
  is preserved, with superseded product scope explicitly marked. Its examples
  are not contracts or current implementation assignments.

## Retained, removed and deferred

| Status | Product capabilities |
|---|---|
| Retained | Exactly two agents: Coordinator and Tutor. Windows C#/WPF; keyboard and NVDA access; optional speech. PDF study, uploaded lectures, YouTube search/selection and supported video playback/analysis. Passage, diagram, graph, table and basic-equation exploration. Source references, relevant lecture timestamps and exact return to reading. Activation hotkey, push-to-talk, press-to-interrupt and immediate local STOP. Deterministic navigation outside model reasoning. Optional understanding checks and factual activity/answer/assistance history. |
| Removed from current requirements | Automatic learning/mastery labels and spaced-review scheduling; assessment-platform features beyond optional checks and their records; browser extensions and automatic screenshots; sonification and specialized code navigation. |
| Deferred | Drive integration, general web ingestion and additional source formats; braille integration; hands-free voice interruption and echo cancellation. |

YouTube discovery remains in scope even though general web ingestion is deferred.
Existing code, schemas, databases and projections for removed features remain
untouched by this migration. Their presence does not reinstate product requirements.

## Evidence and teaching

Coordinator follows question → retrieve → inspect → detect evidence gap → change
strategy → retrieve again → validate → answer → adapt. A sufficient first result
can proceed directly. An unresolved gap leads to useful clarification or an honest
statement of the limitation, within the originating budget. Identical failed
retrievals must not become an unproductive loop.

Tutor explains and adapts using source evidence, observed responses and the
student's stated reasoning. It does not diagnose mastery or store an inferred
misconception as an established fact. Checks are optional. Learning service
validates and commits proposals, including delivered study activity, answers,
student-stated reasoning, feedback and assistance. Preserve each attempt and its
assistance separately; a declined check must not create an answer or grade.

“Studied — understanding not tested” is factual activity wording. It is **not**
a new mastery classification, enum or wire field. Delivered, played and acknowledged
content remain distinct. Do not claim the student heard text merely because it was
generated or sent. Existing models do not yet represent the complete record target;
see the [migration report](../audits/documentation-migration-report.md).

## Harness and recovery

Each agent receives scoped context: current goal, relevant evidence and selected
history. Compact older dialogue when needed, but keep exact canonical positions,
pending questions, source versions and assistance records outside summaries.
Use validated typed handoffs, bounded tools, permission checks, validated inputs
and results, cancellation, retries/timeouts and inspectable action/outcome records.
Do not record private chain of thought. Waiting for a student consumes no model calls.

Services enforce access and source-version checks. Retrieved content is untrusted
data, never permission. Durable state, replay-safe effects, PostgreSQL leases,
at-least-once jobs, retry/backoff and outbox remain required. External calls stay
outside long database transactions. Providers remain behind adapters.

STOP is local and immediate. Cancelled or disconnected generations cannot resume
audio. Only accepted final ASR transcripts submit turns; interim ASR never triggers
commands. Preserve existing request identity, reconnect and pending-question
semantics in [message flow](message-flow.md).

## Budget decision boundary

| Status | Limits and evidence |
|---|---|
| Existing approved baseline | 4 attempted model decisions, 6 total tool invocations, 20-second answer deadline, shared across retries, fallback and delegation. Constants and `TurnBudget` exist in [limits.py](../../api/src/netra_api/coordinator/limits.py); the full Coordinator loop remains a stub. |
| AgentSpec proposal; not approved runtime policy | 8 model-call attempts, 12 tool-execution attempts, 45 seconds, plus a two-substantive-revision cap. Proposed accounting explicitly includes model calls within tools. |
| Required decision | M1 with M3/M4 must review numeric limits, nested model accounting and revision semantics against representative runs. No revision cap is currently approved; do not invent a replacement. |

No code/configuration changes are authorized by this documentation baseline.
The repository's `config.py` is empty; it does not approve alternate limits.

## Video and accessibility gates

Playback readiness and analysis readiness are separate per selected video.
Check supported player access, keyboard/focus operation and actual timestamp capture
for playback; independently check permission, provider access/ingestibility,
processing completion and time-aligned visual/audio evidence for analysis. A URL,
successful playback or transcript-only access does not establish visual understanding.
Pause-and-describe keeps the lecture paused, records its actual position and resumes
that position. Do not observe unrelated browser tabs.

The AgentSpec's embedded YouTube player requires a desktop web-view dependency
decision and M3/M5 integration tests. It is a proposal, not an installed capability.
Do not add a dependency or silently replace a provider. MCP is optional and is not
introduced here; no additional agent framework or deployable service is required.

## Evidence required for acceptance

Prioritize deterministic source/evidence checks, review against original media,
state/recovery tests and keyboard/NVDA task tests. Use the AgentSpec's chapter and
lecture acceptance fixture to test sufficient-first-result, missing-axes repair,
unreadable evidence, exact return position, preserved attempts and assistance,
duplicate delivery, reconnect and cancelled audio.

The revised [17 September model/evaluation plan](model-evaluation-plan.md) selects
Prometheus-2 7B on Modal/A100 40 GB as the primary model judge, alongside
deterministic/source checks and human calibration. AWS GPU access is not required;
Lightning AI and Kaggle are explicit alternative hosts. This external offline
scorer is not a product agent or student-turn dependency. Hosted evaluation remains
an implementation/acceptance milestone; outage leaves it incomplete without
blocking student operation. Ragas-style metrics use repository scripts; Ragas
remains dependency-blocked. See [evaluation dependencies](runtime-baseline.md#evaluation-dependencies).
A blindfolded sighted teammate exercise tests interaction only; it does not
establish blind-student usability. Record live versus fixed/replayed responses and
the actual checks performed. A PDF-only demonstration remains incomplete against
the video target. See the [integration checklist](../team/integration-checklist.md).
