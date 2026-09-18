# Continue the existing builds and integrate M1–M5

Prepared 18 September 2026. This is a coordination plan grounded in fetched Git
history and member handoffs, not a claim that all branches are integration-ready.
Use the [AX plan](../architecture/arize-ax-integration.md),
[acceptance checklist](integration-checklist.md) and [dependency review](dependency-review.md).

## Observed starting point

| Work | Observed commit / location | Integration consequence |
|---|---|---|
| Remote main | `41ee8c7` | Already includes the earlier M3 and M4 builds. |
| AX documentation | `d6a0ca2`, following `f9ef024` and `0d29d07` | Publish/adopt the latest documentation branch; the last decision supersedes earlier evaluator choices. |
| M1 | Local `codex/m1-coordinator`, `63c7e28`, separate worktree | Implementation exists locally; no remote M1 branch was visible. Publish/review it before another member depends on it. |
| M2 | User confirms the build is on a friend's laptop, not GitHub | Status, source, dependency changes and test evidence are unknown until M2 publishes a reviewed branch/report. |
| M3 | `25cdbc2`, remote `codex/m3-multimedia` | Ancestor of main. Request a follow-up delta, not another merge of the same build. |
| M4 | `1f677b0`, remote `codex/m4-tutor` | Ancestor of main. Request a follow-up delta, not another merge of the same build. |
| M5 | `9d1c94e`, remote `codex/m5-client` | Published, not an ancestor of main; still needs review/integration. |

Re-fetch and verify full SHAs before acting. These short SHAs identify the inspected
snapshot, not permanently current branch tips. No teammate implementation was
merged or pushed by preparing this playbook. Publication of the documentation
branch does not itself merge it into main.

## Send these continuation prompts

Give each member their complete continuation prompt; it applies to work already
done and reads their original implementation prompt as the acceptance scope.

- [M1: existing Coordinator build and shared tracing](prompts/updates/M1.md)
- [M2: publish local data build, dependency resolution and foundations](prompts/updates/M2.md)
- [M3: complete media integration and source truth](prompts/updates/M3.md)
- [M4: complete Tutor, factual history and AX/Prometheus experiments](prompts/updates/M4.md)
- [M5: complete real desktop, speech and playback integration](prompts/updates/M5.md)

All five report using the [handoff template](handoff-template.md). Report first,
then continue independent work; a missing upstream merge must not stop unrelated
implementation. After each consumed merge, run the post-merge prompt below.

## Integrate slices in this order

Do not wait for five supposedly finished branches and merge them in numeric order.
M1 and M2 have mutual interface dependencies; settle/split those foundations before
domain wiring. An earlier build being merged does not establish product completion.

| Wave | Deliverable and merge order | Gate / consumers |
|---|---|---|
| 0 | Review/merge the published documentation update into the agreed integration base. Collect all five current handoffs; M1 and M2 publish their existing builds for review. | Exact base/head SHAs and upstream blockers are visible. Preserve local changes. |
| 1 | Joint boundary review led by M1: M1/M2 persistence/auth/evidence contracts; M2/M3 sinks/jobs; M1/M4 history and Tutor adapter; M1/M3/M5 source/video/audio contracts; M3/M4 grounding. | Record approvals or precise unresolved decisions. Do not guess protocol fields or policies. |
| 2a | M2's minimal dependency/lock slice; M1's reviewed shared interfaces, base Coordinator integration and tracing interface/lifecycle. Review overlapping foundational changes together and sequence individual PRs by their declared prerequisites. | A common runtime and callable boundaries exist; M1's base is not yet the fully wired product. |
| 2b | M2's reviewed migrations, source/evidence repositories, jobs/outbox and sink implementations; domain owners review their record/write semantics. | Real local persistence and migration checks; stable evidence versions; no competing migration heads. |
| 3 | M3 media adapters/sinks/provenance delta and M4 Tutor/history/outbox delta consume the foundations. These can proceed in parallel; M4's media-dependent grounding consumes M3's reviewed evidence boundary first. | Actual registered services, original-source checks and atomic factual-history behaviour. Existing M3/M4 commits are already in main. |
| 4 | M1's final API composition, authentication/session/source routes, provider registration and domain tracing wiring consume M2/M3/M4 deliveries. | Real API integration, replay/version/budget/cancellation and trace-completeness tests. |
| 5 | Review/merge M5's existing client build plus follow-up real API/audio/player integration against the reviewed contracts and running API. | Actual desktop study, playback/STOP/return and Windows/NVDA evidence; fixture views are not production delivery. |
| 6 | M4's completed AX/Modal experiment integration consumes the stable end-to-end path and all owners' fixtures/measurements. | Calibrated external scores, confirmed AX traces/experiments, resumable runs and reproducible before/after comparison. |
| 7 | Joint integration regression and readiness report; merge the tested candidate into main through normal review. | Every required gate passed or explicitly reported incomplete; no implied production deployment. |

Evaluation tooling, rubric development and M5 UI work can start before their final
wave. M5's independently passing shell slice may merge earlier if its fixture
status remains explicit. M4's offline tooling can also merge before the live
product path; wave 6 is the full integration gate, not a ban on parallel work.
If a reviewed upstream PR has not merged, use an explicitly recorded stacked PR
based on that exact upstream commit. Retarget/retest after it lands; do not create
a second independent copy of its code or pretend the dependency is satisfied.

## Known blockers to revalidate in member reports

These come from committed handoffs, not fresh runtime verification. Each owner
must check its current implementation before retaining or closing an item.

| ID | Dependency / required result | Provider → consumer |
|---|---|---|
| INT-01 | Publish M2 source, manifest diff, migrations, real test output and branch SHA. | M2 → all |
| INT-02 | Reviewed M1 identity/session tables and migrations; authenticated source/reading repositories and atomic session replay. | M1 record semantics + M2 storage → M1/M5 |
| INT-03 | Evidence/source version identity compatible with the typed handoff; missing versions currently block M1 delegation. | M2 + M1/M4 review → M1/M3/M4 |
| INT-04 | `TableCandidateSink`, `ExtractionCandidateSink`, `VideoEvidenceSink`, `ProviderBindingSink` and lease-aware `StageRecorder`. | M2 implementation + M3 review → multimedia jobs |
| INT-05 | M3 real extraction/Marengo/Pegasus/Tavily adapters and reviewed media/source validation, beyond ports/fixtures. | M3 → M1/M4/M5 |
| INT-06 | M4 D2: optional-check grounding and evidence references; default fail-closed validator is still a blocker until implemented/reviewed. | M3/M4 with M2 resolution → Tutor |
| INT-07 | M4 D3: factual activity/assistance/reasoning/feedback, correction provenance and any approved handoff change. Distinguish generated/sent/played. | M4 shape + M2 persistence + M1/M5 consumers |
| INT-08 | M4 D4: transactional attempt/outbox boundary and factual Neo4j projection without removed mastery logic or fabricated worker authority. | M2 + M4 → learning/worker |
| INT-09 | `TutorRunner` adapter around M4 `run_turn`/`build_turn_state`, shared budget, pending-question repository; do not re-commit events carrying `attempt_id`. | M4 + M1 → API composition |
| INT-10 | Credential/session/source-selection routes, upload/status, evidence and video playback/discovery transport; M1 proposals are not automatically approved contracts. | M1/M2/M3/M5 review → client |
| INT-11 | Real microphone/ASR, TTS output and incremental audio playback; review frame-size/ack semantics and any playback/WebView dependency. | M1/M5 with M3 video → desktop |
| INT-12 | AX common tracing interface, allowlist, exporter lifecycle and ID mapping; domain spans and complete run reconciliation. | M1 + domain owners → M4 comparisons |
| INT-13 | Full runtime lock/install validation; no lock exists in the inspected branches. AX and GPU environments need separate reviewed pins. | M2 with M1/M4 → all Python owners |

Other decisions (database placement, nested model accounting, result-set retention,
MathML fidelity and shortcuts) stay explicit in owner reports. Do not make
multi-host scaling a prerequisite for this workload; preserve the approved
deployment and verify correctness within its actual process configuration.

## Task prompt: integration inventory and boundary decisions

```text
Act as Netra integration coordinator. Read AGENTS.md, CLAUDE.md, the current
scope/runtime/contracts, docs/team/integration-playbook.md and all five current
handoff reports. Fetch origin, inspect exact commits and verify which changes
are already merged. Do not treat historical test counts as current evidence.
Build docs/team/integration-status.md using the handoff template's blocker fields.
For every gap identify producer, consumer, exact symbol/schema, prerequisite PR,
test evidence and the smallest change that unblocks it. Separate code not written,
code not wired, unmerged upstream code, environment failures and policy decisions.
Review INT-01 through INT-13 with their named owners. Record concrete proposed
contracts and required approvals; do not silently approve missing product policy.
Break circular M1/M2/M4 dependencies into shared interfaces, migrations, adapters
and final wiring. Publish an ordered PR list with exact base/head SHAs and gates.
Continue independent analysis while reports are missing. Do not merge teammate
code or claim readiness in this task. Commit the reviewed integration-status report
on an integration coordination branch and report remaining owner decisions.
```

## Task prompt: dependency and shared-foundation review

```text
Act as M2 dependency/migration coordinator with M1/M3/M4/M5 interface review.
Read docs/team/dependency-review.md and the current handoffs. Reproduce each
reported resolver/import/build error with the exact command, interpreter, OS,
manifest/lock hash and selected extras. Distinguish a dependency conflict from
missing tooling, wrong runtime, import-path errors and an unmerged interface.
The user authorizes necessary manifest/lock repairs: make the smallest compatible
change and update the runtime authority in the same PR. Do not change models,
frameworks or the baseline runtime to avoid the error. M2 is the sole root-lock
writer; gather exact pin requests from M1, and keep M4 AX/GPU environments isolated.
Use an existing authorized resolver to lock the shared API/worker environment
with the recorded freeze date; do not remove the cutoff to make a solve pass.
Dependency installation/runtime downloads and live calls remain separately scoped.
Validate on actual Python 3.13.15/deployment platform when available and report
unrun checks. Review contract shapes before serializing migrations; never rewrite
an applied migration. Deliver dependency and migration PRs separately where useful,
with downstream owners, exact test evidence and remaining blockers. No main merge.
```

## Task prompt: set up isolated test environments

Invoke this when package/runtime installation is intended. It supplies the setup
scope deliberately excluded from ordinary continuation/review prompts.

```text
Prepare isolated Netra development/test environments for the agreed integration
commit. Read the runtime baseline, dependency review and relevant owner handoffs.
This task authorizes downloading/installing the approved runtimes and dependencies
into isolated project/test environments, not changing global environments or
upgrading pins. Confirm the target is disposable development/test, never an
unspecified shared or production database. Preserve existing environments and data.
Use Python 3.13.15 for API/worker and the pinned .NET SDK for WPF; record actual
platforms and exact setup tool versions. If the shared uv.lock is missing or stale,
coordinate its reviewed generation with M2 first; do not improvise separate locks.
Install from the reviewed lock without upgrades. Keep AX experiment and Modal GPU
environments isolated with their own reviewed pins. Use only the approved database
placement/configuration; report unresolved placement rather than choosing for owners.
Run dependency consistency/import checks, real pytest and appropriate build checks;
record package hashes, commands and limits. No credentials, live providers, paid
resources, production migration or deployment are authorized by this setup task.
Return reproducible setup instructions and exact remaining environment failures.
```

## Task prompt: review one integration PR

```text
Review the explicitly selected Netra PR at its exact head against the agreed
integration base. Read current authorities, the author handoff and declared
prerequisite PRs. Verify ancestry rather than relying on branch names.
Inspect the actual diff for ownership, contract compatibility, authorization,
source versions, atomicity/idempotency, cancellation/budgets, private data and AX
response-path isolation. Check dependency/lock changes with M2 and all affected
consumers. Run meaningful available tests without implicit installation or live
provider calls. Review fixtures separately from actual persistence/UI/provider
evidence. Report actionable findings with file/line, severity and acceptance case;
do not manufacture blockers for optional scale work. Return ready-to-merge or
blocked with exact prerequisites and unrun gates. Do not merge or post external
comments automatically. Approval applies only to the reviewed head and base.
```

## Task prompt: merge the next reviewed slice

Use only after a specific PR/head/base and its reviews are recorded. This is an
explicit merge instruction when invoked with that selection, not permission to
merge every available branch.

```text
Integrate the selected reviewed Netra PR into the agreed integration branch.
Verify the target, approved head SHA, current base and prerequisite merges.
If the reviewed head changed, re-review before merging. Use a clean isolated
checkout/worktree and preserve user edits. Apply the merge, inspect conflicts
semantically and coordinate changed owner contracts; never choose ours/theirs
wholesale for manifests, schemas or migrations. M2 reconciles the lock from the
resolved manifest instead of text-merging lock fragments. Do not overwrite applied
migrations. Run the affected producer/consumer tests and integration checks; a
missing required check leaves the candidate pending rather than ready for main.
Update docs/team/integration-status.md with resulting SHA, failures and unblocked
owners. Commit and publish only the reviewed integration branch; no force push,
production deployment or unselected main merge. If a required gate fails, keep
the candidate isolated and report it rather than publishing a success claim.
```

## Task prompt: resume after an upstream merge

Send this to every consumer named in the merged slice's handoff.

```text
Continue your existing Netra role build after the specified upstream merge.
Inspect status and preserve local edits. Fetch and verify the exact merged SHA;
incorporate the agreed integration base without resetting or rewriting shared
history. Read the producer handoff and compare actual signatures/schema versions.
Replace only the test-only adapters/fixtures that the real dependency now satisfies;
do not report an unregistered service as integrated. Implement remaining owned
wiring, fix affected consumer tests and run the actual producer-consumer journey.
Keep unrelated blocked work separate. Update your handoff: blockers closed with
commit/test evidence, remaining blockers with producer/consumer/next action, newly
discovered gaps, dependency diffs, real versus mocked coverage and next merge slice.
Commit reviewed owned changes and report the PR/base/head needed next. No force
push, unapproved contract decisions or automatic merge to main.
```

## Task prompt: full AX/Prometheus experiment verification

```text
Act as M4 evaluation owner with M1 tracing and M2/M3/M5 evidence. Read the AX and
model/evaluation plans plus the integration checklist. On the agreed integrated
commit, complete reference dataset, error analysis, named evaluations, calibrated
rubrics and external Prometheus-2 judging. Preserve held-out cases and independent
artifacts. Confirm trace/case/run mappings, recover partial and ambiguous uploads,
and compare frozen baseline/candidate outputs with identical judge settings.
Report missing cases, paired regressions, deterministic/source/accessibility
failures, calibration disagreement, trace completeness, overhead and budget use.
Exercise AX/judge outage and resume without unnecessary producer/judge calls.
Use local fixtures first. For live execution, use only explicitly authorized
accounts, secrets, deployment and credit caps; if these are absent, finish local
work and report the exact live gates as pending. A successful mock is not live
completion. Record actual Modal shutdown and AX ingestion when performed.
Deliver a reproducible report and commands, not only dashboard screenshots.
```

## Task prompt: authorized live AX/Modal acceptance

Use only when the team is ready to authorize external execution with an explicit
reviewed dataset/run allowance and configured account access. This prompt supplies
that limited execution scope when invoked; it is not executed by publishing docs.

```text
Execute the reviewed Netra AX/Modal acceptance run for the selected integrated
commit and frozen permitted dataset. This task authorizes AX dataset/experiment
uploads, reviewed Modal scorer deployment and Prometheus inference within the
existing credits and the recorded bounded run allowance. Read the AX/judge plans
and verify local acceptance, exact dependency/model/image pins and remaining
credit/cap controls first. If dataset, run allowance, account entitlement or
approved secret configuration is missing, ask only for that missing input.
Use the approved secret mechanism without exposing values, proxy authentication,
one A100-40GB container, scale-to-zero and zero paid overage. No AWS GPU, tier
upgrade, new payment method, public unauthenticated endpoint or unrelated account
changes. This does not authorize live student-agent providers unless separately
included in the selected run scope; frozen candidate outputs can be judged first.
Run human calibration before accepting held-out aggregate scores. Persist outputs
and results, confirm AX IDs/traces, verify resume/reconciliation and paired reports.
Stop on exhausted allowance or unscored failures without model substitution. Bound
server work after client timeouts; explicitly stop the deployment and verify final
usage/shutdown. Report calibrated scores, disagreements, omissions, actual cost,
trace completeness and remaining live product/Windows/NVDA gates. No main merge.
```

## Task prompt: final integration and release-readiness review

```text
Review the exact integrated Netra candidate with all five current handoffs.
Execute the integration checklist across PDF and video, authorization/source
versions, factual learning history, retries/concurrency, STOP/reconnect, real
Windows/NVDA/audio and AX/Prometheus comparison. Use the approved runtime/lock;
label unavailable environments and fixtures honestly. No paid/live calls unless
already authorized for this run. Verify one migration lineage, independent
evaluation artifacts, no private tracing content and no high-volume scope creep.
Update integration-status.md with each gate's evidence and remaining defect owner.
Recommend ready or incomplete with exact reasons; do not average away a critical
failure. Prepare the final PR description around actual resulting behaviour and
validation. Do not merge main or deploy as part of this review prompt.
```

## Task prompt: publish the accepted candidate to main

```text
Merge the specifically approved Netra integration PR into main and publish it.
First verify the approved candidate SHA, current main, required reviews and final
acceptance report. Revalidate if either head/base changed. Do not bypass branch
protection, required checks or unresolved critical failures; keep an incomplete
candidate on its integration branch. Use the repository's normal merge mechanism,
never force push. Verify remote main contains the accepted commit and report its
SHA plus remaining explicitly accepted limitations. This is source publication,
not permission to deploy infrastructure, run providers or spend credits.
```
