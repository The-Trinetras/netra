# Netra runtime and dependency baseline

Review cutoff: 11 September 2026.

Status: Release and declared dependency metadata checked.
Full dependency locking, installation and integration tests remain pending.

The existing Netra engineering plan is the architectural source of truth.
This document constrains runtimes and dependencies; it does not authorize
architecture changes or provider replacement.

## Runtime baseline

- API and worker: CPython 3.13.15, conventional GIL build.
- Both processes must share the Python version, base image and dependency lock.
- Python project requirement: >=3.13.15,<3.14.
- Pin .python-version to 3.13.15.
- Desktop: C# WPF targeting net10.0-windows with UseWPF enabled.
- Pin .NET SDK 10.0.401 in global.json.
- Set SDK rollForward to disable and allowPrerelease to false.
- Initial .NET Desktop Runtime baseline: 10.0.12.
- WPF runs on Windows; backend containers run on Linux.
- Do not enable preview C# features or free-threaded Python.

## Infrastructure baseline

| Component | Version |
| --- | --- |
| PostgreSQL | 17.11 |
| Neo4j | 5.26.30 LTS |
| Nginx | 1.30.4 |
| Docker Engine | 29.7.2 |
| Docker Compose plugin | 5.3.1 |
| Tesseract executable | 5.5.3 |
| Redis | Disabled; 7.4.10 only if separately approved |

Use Docker Compose on one EC2 instance.
Use the Compose Specification and the docker compose command.
Do not add Kubernetes or EKS.
Pin container images to explicit versions and verified digests before deployment.
Record the Tesseract binary build and OCR language-data versions.

## Dependency authority

pyproject.toml defines approved constraints.
The committed uv.lock defines exact direct and transitive Python versions.
global.json defines the .NET SDK.
Committed NuGet declarations and lockfiles define desktop package versions.

Do not overwrite existing monorepo package metadata to apply this baseline.
If repository files disagree, report the conflict before changing versions.

Core agent pins:
- langgraph==1.2.11
- langchain-core==1.6.2
- langgraph-checkpoint-postgres==3.1.2

Provider pins:
- google-genai==2.21.0
- groq==1.7.0
- deepgram-sdk==7.8.1
- elevenlabs==2.65.0
- pinecone==9.1.0
- neo4j==6.3.0
- twelvelabs==1.3.4
- tavily-python==0.7.27
- llama-cloud==2.14.1
- boto3==1.43.92 (approved 2026-09-12; declared in pyproject.toml. uv.lock
  regeneration is blocked because uv is not installed in this environment —
  pip dry-run resolution confirmed the pin and its full dependency set
  resolve cleanly, so the pin itself is not in question, only the lockfile
  mechanics.)

Use HTTPX for Jina Reader.
LlamaParse remains the parsing provider; llama-cloud is its selected SDK.
The Groq Tutor model remains openai/gpt-oss-120b.
The Coordinator Gemini model is the approved configured pin gemini-3.8-flash
(2026-09-12, api/src/netra_api/coordinator/providers/gemini.py) — a
configuration decision, not an independently verified claim that this model
id currently exists on the provider's API.

## Rules for Claude Code

1. Never use "latest", floating Git branches, wildcard image tags,
   or unbounded dependency declarations as version recommendations.
2. Never add, remove, upgrade, downgrade or re-resolve dependencies
   without explicit approval. A permitted range is not upgrade permission.
3. Use repository-defined versions and documentation matching those versions.
4. Install dependencies only when explicitly requested.
   This includes Python installation, NuGet restore, runtime downloads,
   and commands that implicitly synchronize environments.
5. Do not delete or regenerate lockfiles to work around a conflict.
6. Keep every provider SDK behind an adapter. Convert provider objects
   into Netra-owned models before returning them to application code.
7. Keep model IDs, endpoint versions, parser versions, embedding dimensions
   and SDK versions as separate explicit configuration.
8. Do not change providers or models to make dependency resolution easier.
9. Report security advisories promptly and propose a reviewed update.
   Do not silently upgrade, and do not treat pins as permanent immunity
   from maintenance.

## Compatibility rules

- Application models use Pydantic v2.
- Do not independently override pydantic-core.
- Do not add the full LangChain or LlamaIndex frameworks.
- Keep websockets >=16.1.1,<17 for the selected Gemini SDK.
- SQLAlchemy application access uses postgresql+asyncpg connections.
- PostgreSQL checkpointing uses Psycopg, not an asyncpg pool.
- Follow checkpoint-saver setup requirements on checkpoint connections only.
- Use Neo4j 6.x driver APIs and Neo4j 5.26-compatible queries.
- Use Pinecone 9.1 APIs, not examples from another SDK major.
- Use Deepgram 7.x and ElevenLabs 2.x streaming interfaces.
- WPF owns microphone access, playback and immediate local interruption.
- PostgreSQL remains authoritative; Neo4j is rebuildable.
- Redis is never required for correctness, checkpoints or durable jobs.

## Completing the freeze

When dependency setup is explicitly requested:

- Resolve and commit the full Python lock without pre-releases.
- Validate installation on the actual deployment Python/platform.
- Record and verify image digests.
- Run API/WebSocket, migration, async database and checkpoint-resume checks.
- Run adapter contract tests and approved bounded live-provider checks.
- Build and test the WPF client on Windows.
- Record the tested versions and results.

Until these checks pass, describe this baseline as metadata-checked,
not installation-tested or production-validated.

Subsequent approved installs must use the committed lock without upgrades.
Prefer --locked for uv synchronization so manifest/lock disagreement fails.
When installation is not authorized, avoid implicit synchronization or restore.