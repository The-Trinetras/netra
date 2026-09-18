# Integration status

Living record for the M1–M5 integration branch. Update it at every slice so work
can continue after a context reset without repeating investigations. Evidence
classes are kept separate: **fixture** (doubles), **real local** (real processes,
databases or sockets on this machine), **Windows/NVDA** (assistive-technology
sessions with a person) and **live** (provider/AX/Modal). Nothing here claims
production readiness.

## Checkpoint

- **Current slice:** A — clean runtime and combined baseline.
- **Next action:** start the disposable PostgreSQL, apply migrations, run the
  19 `integration` tests (slice B).

## Identity and environment

| Item | Value |
|---|---|
| Branch / worktree | `codex/netra-integration` at `D:\Agentathon\netra-integration` (no upstream; unset so a bare push cannot target `main`) |
| Starting SHA | `origin/main` `4ede791b102eea30172c535286726c1b4ee4836e` (Merge PR #14, M2) |
| Verified ancestors | M1 `162e50b`, M2 `79a7078`, M3 `d45cb54`, M4 `16d1859`, M5 `903512a`, docs `f967f43` — all ancestors of `4ede791` (`git merge-base --is-ancestor`). No re-merge needed. |
| Other worktrees (untouched) | `D:\Agentathon\netra` (`codex/documentation-baseline`), `netra-m1`, `netra-m5` |
| OS | Windows 11 Home 10.0.26200 |
| Python | CPython 3.13.15 (system install, used read-only as the base interpreter) |
| App environment | `D:\Agentathon\netra-integration-envs\app-venv`, created by `uv sync --locked` (uv 0.12.13 in its own tool venv, isolated `UV_CACHE_DIR`, `UV_PYTHON_DOWNLOADS=never`). Installed set = lock minus platform-conditional `uvloop`, `httpx2-jsfetch` and the non-package root project. No extra packages. |
| Env helper | `source D:/Agentathon/netra-integration-envs/env.sh` (Git Bash) sets `UV`, `UV_CACHE_DIR`, `UV_PROJECT_ENVIRONMENT`, `PY` |
| Lock | `uv.lock` revision 3, 106 packages, `exclude-newer 2026-09-12T00:00:00Z`, prereleases disallowed; `uv lock --check` exit 0 |
| .NET | SDK 10.0.401 (matches `client/global.json`) |
| Docker | Engine 29.5.3 / Compose v5.1.4 locally (deployment baseline 29.7.2 / 5.3.1 is not what runs here) |
| Push | `.claude/settings.json` denies `git push`; the user publishes the branch |

## Test counts (latest)

| Command | Collected | Passed | Failed | Skipped | Deselected |
|---|---|---|---|---|---|
| `$PY -m pytest -q -p no:cacheprovider` at `4ede791` (before fixes) | 1056 | 1035 | 2 | 0 | 19 |
| same after slice A fixes | 1057 | 1037 | 0 | 1 | 19 |

The default configuration deselects `integration` tests (`addopts = -m 'not integration'`):
a green default run is **not** database acceptance.

## Capability map

Status: working (real local evidence) · unwired (implemented, not composed) ·
incomplete (partial) · unverified (fixture only) · blocked (decision/hardware/live).

| Capability | Status | Evidence / gap |
|---|---|---|
| Locked 3.13.15 environment | working | clean `uv sync --locked`; default suite green |
| PostgreSQL migrations 0001→0007 | unverified | slice B |
| M2 repositories, jobs, outbox | unverified | 19 integration tests not yet run |
| FastAPI app boot, real WebSocket | unverified | slice C |
| LangGraph integration | unverified | now importable (was skipped in M1 handoff) |
| Coordinator → Tutor on shared budget | unverified (fixture) | `test_m4_tutor_integration.py` |
| Optional-check support (D2) | blocked (decision P-1) | binding enforced; support fails closed |
| Real client connection | blocked on routes/credential (INT-10) | slice D |
| AX exporter | unwired (no OTel pins) | slice E |
| Prometheus/AX evaluation | blocked on live authorization | slice F |

## Defects and blockers

| ID | Defect | Producer | Consumer | Evidence | Fix / acceptance | Status |
|---|---|---|---|---|---|---|
| A-1 | `test_m4_pending_grounding_decision_is_a_bounded_failure_not_a_crash` failed on main | M4 (citation binding added after M1 wrote the test) | M1 transport test | Draft double cited no evidence, so `bind_draft_to_evidence` refused it (`failed`) before the pending support check; traced both paths through the real transport | Fixture now cites the resolved evidence and reaches `tutor_capability_pending_decision`; new separate test proves uncited and unresolved citations are a bounded `failed` result, nothing persisted, no `quiz.question`. Implementation unchanged. | fixed |
| A-2 | `test_locked_real_fixture_reconstructs_all_golden_chunks` fails on every clean checkout | M2 (fixture documents are gitignored, laptop-only) | evaluation suite | `FileNotFoundError` for the third-party PDF | Skips with an explicit reason only when the local-only documents are absent; runs unchanged when present | fixed |

## Decisions needed

| ID | Decision | Recommendation | Blocks |
|---|---|---|---|
| D-LIC | PyMuPDF 1.28.2 is AGPL-3.0 (or commercial) | Project owner decides; the integration does not accept the obligation. Alternative: LlamaParse + Tesseract only. | Distribution, not local integration |

## Independent work that can proceed

Everything in slices B–E that needs no live provider or new product policy.
