# Required M1–M5 integration handoff

Each member updates their own `docs/team/handoffs/M1.md` through `M5.md` using
this structure. Preserve useful earlier evidence, but mark it with its actual
commit/runtime/date and supersede obsolete claims. No credentials, private data,
signed URLs or unrestricted environment dumps belong in reports.

## Identity and state

- Owner, date, branch, full head SHA, remote URL/PR and reviewed base SHA.
- Published / local-only / merged, with actual merge commit if known.
- Latest documentation and integration commits incorporated.
- Working-tree changes excluded from the reported commit.
- Runtime/OS, dependency manifest and lock hashes; installed versus requested versions.

## Capability status

| Capability | Implemented files/symbols | Status | Evidence at commit | Remaining work |
|---|---|---|---|---|
| Fill one row per required behaviour | Exact path/signature | Not started / partial / implemented-unwired / fixture-tested / real-integration-tested / live-tested | Command, result, runtime/date and artifact | Concrete next step |

Cover both original role requirements and AX additions. Do not use one overall
"done" label to hide unimplemented persistence, adapters or client hardware paths.

## Provided boundaries

| Interface/service/schema | Exact signature/version | Caller and ownership | Auth/errors/replay/cancellation | Registration/configuration | Test |
|---|---|---|---|---|---|
| Exported boundary | Include async/sync and input/output types | Producer → consumers | Observable semantics | Constructor/factory plus variable names only | Actual test and fixture |

Include migration revision/parent, transaction boundaries, evidence/source IDs,
trace attributes/correlation and any changed result shape. Separate proposals
from approved contracts and include the approval reference when applicable.

## Blocker and dependency matrix

| ID | Exact incomplete behaviour | Type | Providing owner | Consuming owner | Needed symbol/schema/PR SHA | Can proceed independently | Unblock action and acceptance test |
|---|---|---|---|---|---|---|---|
| Stable identifier, reuse INT IDs where applicable | User-visible effect | Missing code / unwired / unmerged / contract decision / dependency conflict / environment / live access | M-X | M-Y | Concrete requirement, not "waiting for backend" | Work you will do now | Smallest verifiable delivery |

State whether merging an existing commit alone unblocks the item or new code is
still required. Name circular dependencies and propose an interface-first split.
For decisions, give recommendation, alternatives, affected owners and failure
behaviour pending review; do not promote recommendations into approvals.

## Dependency and environment evidence

- Exact failing command and relevant error text, with secrets/paths sanitized.
- Python/.NET/OS, package versions, selected extras, index and resolver version.
- Current versus proposed manifest/lock diff and dependency-chain explanation.
- Root runtime versus isolated evaluation/GPU environment; consumers impacted.
- Distinguish solver incompatibility, missing package/runtime, wrong interpreter,
  import/contract mismatch and unmerged code. Do not fix all by upgrading packages.
- M2 owns root lock reconciliation; send requests, not competing lockfile commits.

## Checks and remaining proof

Record exact commands, exit status, pass/fail/skip counts and reasons. Separate
real pytest from substitute runners, database integration from in-memory tests,
approved runtime from another interpreter, and Windows/NVDA/audio/provider evidence
from fixtures. List every unrun required gate and how/where it can be performed.
For AX include redaction, context isolation, failed export, trace completeness and
measured overhead. For M4 include calibration, dataset/producer/judge versions,
partial-run recovery and confirmed experiment comparisons.

## Proposed next merge slice

- Smallest coherent change, branch/head/base and prerequisite PRs/commits.
- Files owned, other owners' reviews needed and incompatible consumer changes.
- Migration order, runtime/configuration names, rollout effect and recovery plan.
- Exact producer/consumer acceptance command and who runs it.
- Remaining work after that slice merges; consumers to rerun/update.

End with: **ready for review / blocked from merge / integrated but acceptance
incomplete**, supporting evidence, and the next owner action. Do not infer product
readiness solely from a branch merge or passing isolated tests.
