# Netra

Netra targets independent study on Windows through PDF and lecture exploration,
source-grounded tutoring, keyboard/NVDA access and optional speech. Exactly two
agents, Coordinator and Tutor, use bounded services and preserve the reading position.

This repository follows the [agentic-slice-kit](https://github.com/rsimhan/agentic-slice-kit)
layout: the kit's spine and tools sit at the root, and Netra's product keeps its
own folders beside them. The runnable slice for the Agent-a-thon is
[`demo/notes`](demo/notes/): a student asks about their notes, a draft must cite
retrieved passages, code checks the citations, and a gate model judges the
draft and **sends it back** when the evidence does not support it.

## Start here

```bash
cp .env.example .env      # then paste the key from the registration desk
```

In the Codespace everything is installed. Elsewhere, use Python 3.13 and run
`pip install -r requirements.txt` once.

```bash
python -m pytest
```

```bash
python scripts/doctor.py
```

```bash
python scripts/notes.py validate --only q1-resistance-from-table
```

```bash
python scripts/notes.py serve
```

`validate` without `--live` uses scripted replies: it proves the wiring, including
the back-edge (a wrong first draft, a BLOCK, a revision, a PASS), and calls no
model. Add `--live` to use the real models on your OpenRouter key; that spends the
key. `serve` starts the tester page on http://127.0.0.1:8000 (add `--live` for real
answers). The kit's own smoke slice is `python scripts/smoke.py run --stub`.

Only `OPENROUTER_API_KEY` is required. The `SLICE_*` lines choose the models and
the budget fences. `ARIZE_*` turns on the notes slice's tracing, and the
`NETRA_*` lines are only for the full product (below).

## Layout

| Folder | What it is |
|---|---|
| [`slice/`](slice/) | The kit's spine, unchanged: records, append-only store, config, budget fences, the one LLM client, retrieval, human callback, runner |
| [`demo/notes/`](demo/notes/) | Netra's slice on the spine: retrieve, draft, evidence ledger, gate (sends work back), Tutor check question, sessions, uploads, tester page, AX tracing |
| [`demo/smoke/`](demo/smoke/) | The kit's smallest slice (draft and gate) |
| [`web/`](web/) | The kit's expert callback form |
| [`scripts/`](scripts/) | `doctor`, `bakeoff`, `smoke`, `sync_architecture` (kit) and `notes` (the Netra slice CLI) |
| [`tests/`](tests/) | Kit and notes-slice suites at the top level; Netra's cross-cutting suites in subfolders |
| [`docs/`](docs/) | Kit guides ([ARCHITECTURE](docs/ARCHITECTURE.md), [PRINCIPLES-BRIEF](docs/PRINCIPLES-BRIEF.md), [BUILDER](docs/BUILDER.md), [DESIGNER](docs/DESIGNER.md), [VERIFIER](docs/VERIFIER.md), [ON-THE-DAY](docs/ON-THE-DAY.md)), the [port checklist](docs/PORT-CHECKLIST.md), and Netra's architecture, team and audit docs |
| [`corpus/`](corpus/) | The kit's sample corpus; the notes slice's own notes are in `demo/notes/corpus/` |
| [`.devcontainer/`](.devcontainer/) | Codespace image: Python 3.13, kit and Netra dependencies, embedding model baked in |
| [`api/`](api/) | Netra FastAPI API: Coordinator, Tutor, session, identity, content, multimedia, learning |
| [`worker/`](worker/) | Netra durable background work |
| [`client/`](client/) | Netra Windows C#/WPF desktop |
| [`shared/contracts`](shared/contracts/) | Authoritative protocol schemas |
| [`evaluation/`](evaluation/) | Netra evaluation harness, rubrics and golden cases |
| [`infrastructure/`](infrastructure/) | Docker Compose, Dockerfiles, reverse proxy |

One environment and one test run cover all of it. `pytest.ini` configures pytest
for the whole repository, and `conftest.py` isolates each test from the `.env` the
kit loads.

## The full Netra product

For parallel implementation, use the [five paste-ready teammate prompts](docs/team/prompts/README.md)
in separate checkouts or worktrees from the same published baseline.

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

**Models.** `NETRA_OPENROUTER_API_KEY` (the Agent-a-thon key works) runs both agents
through OpenRouter with the approved models: `google/gemini-3.8-flash` for the
Coordinator and `openai/gpt-oss-120b` for the Tutor. `NETRA_GEMINI_API_KEY` then serves
embeddings only. Without an OpenRouter key, the Coordinator uses Gemini and the Tutor
uses Groq directly when their keys are set. The API needs PostgreSQL and reads only
`NETRA_*` variables:

```bash
uvicorn --env-file .env --app-dir api/src --factory netra_api.main:create_app
```

This repository contains scaffolding, typed models and some implementation/tests.
The full study journey is not established as working. Endpoint/dispatcher and agent
loops remain incomplete; dependency locks and deployment wiring are missing.
The [original Engineering Plan](docs/architecture/Netra_Final_Engineering_Plan.md)
and [Phase 10 audit](docs/audits/phase-10-repair-report.md) are historical references.

Available checks, **only with existing matching runtimes and dependencies**, are
`python -m pytest -p no:cacheprovider` from the repository root and
`dotnet test client/Netra.sln --no-restore` from the root. Run neither with an
implicit dependency install/restore. The earlier documentation migration used
documentation checks only; it did not rerun or certify application tests.
