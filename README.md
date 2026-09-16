# Netra

Netra targets independent study on Windows through PDF and lecture exploration,
source-grounded tutoring, keyboard/NVDA access and optional speech. Exactly two
agents, Coordinator and Tutor, use bounded services and preserve the reading position.

Start with the [current product scope](docs/architecture/current-scope.md), then
the [architecture overview](docs/architecture/overview.md) and
[M1–M5 workstream guides](docs/team/ownership.md). Coding agents read
[AGENTS.md](AGENTS.md) or [CLAUDE.md](CLAUDE.md); both point to the same authorities.

| Area | Location |
|---|---|
| FastAPI API / Coordinator / Tutor | [api](api/) |
| Durable background work | [worker](worker/) |
| Windows C#/WPF desktop | [client](client/) |
| Authoritative protocol schemas | [shared/contracts](shared/contracts/) |
| Runtime and dependency policy | [runtime baseline](docs/architecture/runtime-baseline.md) |
| Integration acceptance targets | [checklist](docs/team/integration-checklist.md) |
| Current gaps and migration checks | [migration report](docs/audits/documentation-migration-report.md) |

This repository contains scaffolding, typed models and some implementation/tests.
The full study journey is not established as working. Endpoint/dispatcher and agent
loops remain incomplete; dependency locks and deployment wiring are missing.
The [original Engineering Plan](docs/architecture/Netra_Final_Engineering_Plan.md)
and [Phase 10 audit](docs/audits/phase-10-repair-report.md) are historical references.

Available checks, **only with existing matching runtimes and dependencies**, are
`python -m pytest -p no:cacheprovider` from the repository root and
`dotnet test client/Netra.sln --no-restore` from the root. Run neither with an
implicit dependency install/restore. This migration uses documentation checks only;
it does not rerun or certify application tests.
