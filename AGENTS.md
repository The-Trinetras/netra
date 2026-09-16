# Codex repository instructions

Read [CLAUDE.md](CLAUDE.md) for shared engineering invariants and workflow controls.
Both coding tools use the same canonical authorities:

- [Current scope](docs/architecture/current-scope.md) and [AgentSpec](docs/architecture/Netra-SPEC.md): product target and explicit proposal boundaries.
- [Runtime baseline](docs/architecture/runtime-baseline.md): runtime/dependency authority.
- [Shared contracts](shared/contracts/): wire schemas, versions and typed handoffs.
- [Architecture overview](docs/architecture/overview.md), [data ownership](docs/architecture/data-ownership.md) and [message flow](docs/architecture/message-flow.md): preserved boundaries.
- [M1–M5 ownership and guides](docs/team/ownership.md): work scope, consumers and integration gaps.

Read the relevant Markdown rules in [.claude/rules](.claude/rules/) by responsibility;
Codex must not depend on Claude's automatic path matching. Worker work also reads
the backend-data rule; speech/protocol work requires M1/M5 coordination.

Inspect Git status first and preserve existing edits. Make only authorized changes;
do not stage, commit, push, discard changes, install dependencies, read secrets or
call providers unless the task explicitly authorizes that action. Missing required
sources or genuine decisions block only dependent work. Report exact checks and
limitations. Historical plans and audit claims are not current acceptance evidence.

The completed documentation migration changed documentation only; application
prompt Markdown under `api/src` is runtime material and was left unchanged.
Subsequent explicitly requested implementation may edit the assigned code/tests
under the same ownership, contract, runtime and review controls. Read the
[role prompts](docs/team/prompts/README.md) when starting M1–M5 implementation;
they do not turn pending decisions into approvals.
