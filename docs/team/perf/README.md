# Measured optimization and reliability: instruments, baseline and thresholds

Integration branch `codex/netra-integration`. Baseline revision `8ced847`, with the
instruments in `api/tests/perf/` as the only addition. Every number here comes from
the listed instrument on the listed machine. **None of it is live-provider latency,
AX ingestion, audible audio or NVDA behaviour.**

## Evidence classes

| Class | Instrument | What is real | What is a stand-in |
|---|---|---|---|
| E1 in-process fixture | `api/tests/perf/bench_turns_inmemory.py`, `api/tests/transport/tracing_overhead.py` | M1 transport dispatcher, session service, router, Coordinator engine (LangGraph node and direct), tool gateway, evidence ledger, Tutor gateway validation, speech framing and fencing | In-memory persistence, M2/M3 fixture services, the Tutor runner, a scripted model and a fixture synthesizer |
| E2 real local server | `api/tests/perf/run_journeys.py` (committed once it has run) | PostgreSQL 17, the production composition, uvicorn with HTTP and WebSocket over TCP, M2 retrieval and evidence, M4 Tutor and learning store, the google-genai and groq SDKs through Netra's adapters | Provider HTTP (MockTransport with injected latency) and the synthesizer (paced stand-in) |
| E3 tracing export | `api/tests/perf/bench_tracing.py` | `platform/tracing.py`: allowlist, span handles, batch processor, retries, shutdown | The collector, as in-process exporters (healthy, slow, stalled, failing) |
| E4 live | none run | Not run: no provider, AX or Modal credentials, and no execution authorization in this phase | Not run |

E2 did not run in this pass: Docker Desktop, which hosts the disposable
PostgreSQL, was paused manually, and the CLI cannot resume it.

## Baseline environment

| Item | Value |
|---|---|
| Revision | `8ced84776740abfbc41ff7a92b7948e270072a9f` plus `api/tests/perf/` (the instruments, not yet committed) |
| Machine | Windows 11 10.0.26200, Intel Core i5-13500H (16 logical processors), 15.7 GB RAM, on AC power, desktop session in use |
| Runtime | Python 3.13.15; uv.lock sha256 `17d5a133…edc47` |
| Versions | langgraph 1.2.11, langchain-core 1.6.2, google-genai 2.21.0, groq 1.7.0, SQLAlchemy 2.0.52, asyncpg 0.31.0, uvicorn 0.52.4, Starlette 1.6.0, FastAPI 0.141.1, websockets 16.1.1, httpx 0.28.1, pydantic 2.13.5 |
| Evaluation | `netra-grounded-v1` holds 59 draft cases. No reviewed gold, no calibration and no live Netra outputs yet (provider credentials are missing), so there is **no quality baseline**. Deterministic assertions and the producer boundary are ready (`eval_cli check`: 0 errors, 1 warning). |

## Baseline results

### E3: tracing export (`results/tracing-baseline.json`)

Settings: queue 2048, batch 128, schedule 50 ms, export timeout 200 ms, 2 retries, backoff 50 ms.

| Condition | Span median / p99 / max (µs) | Exported (by the counters) | Counted lost | Collector received (distinct) | Duplicate deliveries | Delivered but counted lost | Queued export calls |
|---|---|---|---|---|---|---|---|
| Disabled, 5 rounds × 5000 | 1.0 / 1.8–5.3 / ≤379 | – | – | – | – | – | 0 |
| Healthy burst, 5 rounds × 5000 | 10.8–11.0 / 15–35 / ≤730 | 2304–2688 | the rest (queue full, counted) | = exported | 0 | 0 | 0 |
| Healthy paced, 2000 | 55.3 / 303 / 518 | 2000 | 0 | 2000 | 0 | 0 | 0 |
| **Slow collector** (0.3 s against a 0.2 s timeout), 600 | 10.9 / 58 / 220 | **0** | **600** | **600** | **1024** | **600** | 2 |
| Failing collector, 5000 | 10.9 / 45 / 256 | 0 | 5000 | 0 | 0 | 0 | 0 |
| **Stalled, then released**, 5000 | 10.9 / 31 / 454 | 1920 | 3080 | 2176 | **640** | **256** | **7** |

- **Burst loss is expected.** The burst deliberately ends spans back to back, faster than
  any study session. A full queue drops spans and counts every one; nothing is
  hidden. Paced spans (about 2000 per second) lose none.
- **The paced span median is a CPU idle-state effect, not tracing cost.** A
  disabled tracer also rises from 1.0 to 5.7 µs when paced, and each component
  (for example sanitizing) slows about 5× between bursts.

### E1: transport navigation (`api/tests/transport/tracing_overhead.py 300`)

| Mode | p50 (ms) | p95 (ms) | Max (ms) | STOP (ms) | Shutdown (ms) | Spans exported / lost |
|---|---|---|---|---|---|---|
| Off | 0.159–0.160 | 0.257–0.333 | 2.3 | 0.09 | 0 | – |
| Local | 0.203–0.204 | 0.259–0.268 | 0.73 | 0.13–0.15 | 0.3 | 301 / 0 |
| Stalled | 0.203 | 0.238 | 0.64 | 0.14 | 1012 (bounded by the 1 s setting) | 0 / 301 |

### E1: in-process journeys (`results/turns-inmemory-baseline.json`, 40 iterations, ABBA arm order)

| Journey | n | Median (ms) | p90 (ms) | Min–max (ms) | Model calls | Notes |
|---|---|---|---|---|---|---|
| Grounded question with repair (3 Coordinator decisions + Tutor), LangGraph | 40 | 6.78 | 8.58 | 3.7–10.3 | 3 | Ends in a question; largest prompt 4280 chars |
| Same, direct engine (no LangGraph) | 40 | 6.82 | 8.08 | 3.1–9.0 | 3 | Same outcome; the LangGraph difference is within noise |
| Same, LangGraph with local tracing | 40 | 7.56 | 9.23 | 4.2–10.8 | 3 | Tracing adds about 0.8 ms per turn |
| Missing evidence (search → stated gap) | 40 | 5.22 | 6.90 | 2.9–25.6 | 2 | – |
| Answer a pending check (search → Tutor evaluation) | 40 | 5.67 | 6.88 | 3.0–9.1 | 2 | The Coordinator spends 2 model decisions on an answer |
| STOP during audio: first frame | 40 | 1.39 | 2.28 | 0.4–3.2 | – | **0 frames after cancel in 40/40** |
| Reconnect and recover (drop during the 1st decision, resume, resend) | 10 | 234.6 to first text | 236.0 | 231.5–236.2 | 4 | Recovered text was "Script exhausted.": see C-1 |
| **Disconnect during a model call: call ran on after its turn was cancelled** | 20 | – | – | – | – | **20/20 orphaned** |
| **Disconnect during a Tutor run: run persisted its question after cancellation** | 20 | – | – | – | – | **20/20 orphaned** |

These are Netra's own CPU and orchestration costs with zero-latency doubles. A
real turn adds PostgreSQL work (E2) and provider time: two to four model
calls, each typically hundreds of milliseconds to seconds (E4, not measured).

## Predeclared acceptance thresholds

Written **before** any candidate change ran. The candidate is measured with the
same instrument and settings on the same machine, and compared with the baseline
files above.

### T-1: tracing export duplicates, false loss and unbounded backlog (M1, `platform/tracing.py`)

The candidate must meet all of the following:

1. Duplicate deliveries are 0 in both the slow-collector and stalled-then-released conditions (baseline: 1024 and 640).
2. Delivered-but-counted-lost is 0 for the slow collector (baseline: 600).
3. For stalled-then-released, spans delivered after the final flush are reported
   in their own counter, and that counter equals the collector's distinct count
   minus `exported` (baseline: 256 silently counted as lost).
4. Queued export calls are at most 1 while the collector is stalled (baseline: 7).
5. After shutdown, `exported + lost == ended` holds in every condition.
6. Healthy-burst span median is at most 1.15× the baseline range (≤ 12.6 µs).
   No span on the response path exceeds 2 ms in any condition.
7. Healthy paced still exports 2000 of 2000, and a failing collector is still counted as 100% lost.
8. `api/tests/platform/test_tracing.py` and `api/tests/transport/test_tracing_journey.py` pass unmodified.

### C-1: child work outlives a cancelled turn (M1, `coordinator/graph.py` and `coordinator/tutor_gateway.py`)

The candidate must meet all of the following:

1. Orphaned model-call completions are 0 in 20/20 disconnect-during-model trials (baseline: 20/20).
2. Orphaned Tutor effects are 0 in 20/20 disconnect-during-Tutor trials (baseline: 20/20).
3. Grounded, missing-evidence, answer and STOP outcomes are unchanged, message for
   message, and frames after cancel stay at 0 in 40/40.
4. Grounded-repair median is at most the baseline plus 1.0 ms. Run-to-run noise
   between two baseline runs was 0.7 ms.
5. New regression tests fail on the baseline code and pass on the candidate. The full default suite passes.
6. The recovered turn after a disconnect spends the shared budget as specified:
   1 decision before the drop, then the rest after, with no extra budget. It is
   not required to succeed.

**C-1 result** (`results/turns-inmemory-c1-candidate.json`, same instrument and settings):

| Threshold | Baseline | Candidate | Met |
|---|---|---|---|
| 1. Orphaned model-call completions | 20/20 | 0/20 | yes |
| 2. Orphaned Tutor effects | 20/20 | 0/20 | yes |
| 3. Message kinds and STOP | – | identical for all 9 journeys; 0 frames after cancel in 40/40 | yes |
| 4. Grounded-repair median | 6.78 ms | 6.88 ms (limit 7.78) | yes |
| 5. Regression tests and suite | both tests fail | both pass; 1108 passed, 1 skipped | yes |
| 6. Budget after a drop | orphan consumed the scripted step | 1 decision before the drop plus 3 after, no extra budget; the reply exposed C-2 | yes |

Rollback: `git revert` the C-1 commit.

### C-2: budget exhausted inside the Tutor is reported as "service unavailable" (M1, `coordinator/graph.py`)

Found while measuring C-1. The engine's documented state machine says a limit
ends the turn "with supported findings + stated gaps" (`_limited`), and the
deadline case does that. A `TurnBudgetExceededError` from the Tutor (decision or
tool limit reached during delegation) instead reaches the generic handler, and
the student hears "I can't answer that right now because a required service is
unavailable."

The candidate must meet all of the following:

1. When the Coordinator delegates with its 4th decision and the Tutor needs one
   more, the reply starts with "I stopped before finishing this answer." and
   lists the supported findings. The trace records `budget_exhausted` with
   `limit=model_decisions`. No pending question is persisted.
2. In the in-process reconnect-and-recover journey, the recovered text starts
   with "I stopped before finishing this answer." (after C-1: "I can't answer
   that right now because a required service is unavailable.").
3. Message kinds for every other journey are unchanged. The budget is not
   enlarged: decisions used stay at or below 4.
4. The new regression test fails on the pre-fix code, and the full default suite passes.

**C-2 result** (`results/turns-inmemory-c2-candidate.json`; the C-1 candidate is its baseline):

| Threshold | Before | After | Met |
|---|---|---|---|
| 1. Delegation with the 4th decision | "I can't answer that right now because a required service is unavailable." | Limit reply with supported findings; `budget_exhausted` with `limit=model_decisions`; no pending question | yes |
| 2. Recovered text after a drop | the unavailable message, 10/10 | "I stopped before finishing this answer. Supported by the material: …", 10/10 | yes |
| 3. Other journeys and budget | – | message kinds identical; decisions used 4, never more | yes |
| 4. Regression test and suite | test fails | test passes; 1109 passed, 1 skipped | yes |

Rollback: `git revert` the C-2 commit.

## Reproduction

From the repository root, with the app environment active:

```text
PYTHONPATH="api/src" python api/tests/perf/bench_tracing.py --label <label> --out docs/team/perf/results
PYTHONPATH="api/src;api/tests/transport" python api/tests/perf/bench_turns_inmemory.py --label <label> --out docs/team/perf/results --iterations 40
python api/tests/transport/tracing_overhead.py 300
PYTHONPATH="api/src;worker/src" python api/tests/perf/run_journeys.py --database-url <disposable local database URL> --label <label> --out docs/team/perf/results
```

`run_journeys.py` refuses any database URL that is not local. It is committed only after its first real run.
