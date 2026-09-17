# Model and evaluation plan

Decision date: 17 September 2026, revised at the user's request. **Prometheus-2
7B on Modal is the selected primary model evaluator.** This supersedes the
earlier Gemini-judge/deferred-Prometheus plan in commit `f9ef024`.
Implementation, account eligibility and deployment are not yet verified.

## Selected configuration

| Responsibility | Selected plan |
|---|---|
| Coordinator / M1 | Retain Gemini `gemini-3.8-flash`. |
| Tutor / M4 | Retain Groq `openai/gpt-oss-120b`. |
| Primary model evaluator / M4 | `prometheus-eval/prometheus-7b-v2.0`, hosted on Modal using one A100 40 GB. |
| Acceptance evidence / all owners | Deterministic tests, original-source review, calibrated judge results and human adjudication; scores alone do not establish correctness. |
| Alternative evaluation hosts | Lightning AI for the same isolated scorer; Kaggle for resumable notebook batches. Neither is an automatic fallback. |
| GLM-5.2 | OpenRouter's `z-ai/glm-5.2:free` is a verified public free listing; comparison candidate, not a replacement for the selected evaluator or live agents. |

Prometheus-2 is specifically trained for rubric-based evaluation and supports
absolute and pairwise grading. That makes it a suitable dedicated judge for this
English-language project; Netra-specific reliability still requires calibration.
Use the exact 7B checkpoint, not the much larger 8x7B variant. See the
[model card](https://huggingface.co/prometheus-eval/prometheus-7b-v2.0).

The evaluator is a bounded offline workflow, not a third agent. Modal is a narrow
external evaluation-compute exception to the repository's no-new-services rule,
not a migration of the API, worker, databases or student response path.
AWS GPU provisioning remains excluded. EC2/Compose, exactly two agents, the
4/6/20 originating-turn budget, wire contracts and storage authority remain intact.

## Modal compute and budget

Public [Modal pricing](https://modal.com/pricing) lists $30/month included
compute on Starter and A100 40 GB at $0.000583/second. Calculations:

| Calculation | Result |
|---|---|
| GPU-only hourly rate | $0.000583 × 3,600 = $2.0988/hour |
| $30 divided by GPU-only rate | 14.294 hours; an upper bound, not all-in runtime |
| Initial planning envelope: 12 billed GPU hours | $25.1856 GPU cost, leaving $4.8144 from unused $30 credits |

CPU, RAM, startup/model loading, idle scale-down time and applicable storage
charges reduce available inference time. The 12-hour envelope is provisional:
measure total cost during a small pilot and shorten it if necessary. Fourteen
hours is not a guaranteed continuous session. Credits may already be partly
consumed by other workloads; use the actual remaining balance.

Modal's [GPU guide](https://modal.com/docs/guide/gpu) requires a valid payment
method for GPU use. Account setup/card entry remains a user action. Explicitly
request `A100-40GB`; do not silently select a pricier GPU or multiple replicas.

Planned deployment controls, to be implemented before an authorized live run:

- One GPU, `max_containers=1`, `min_containers=0`, `buffer_containers=0`,
  one in-flight grading request initially, and `scaledown_window=60` seconds.
  No warm pool, periodic keepalive or multiple independently scaling functions.
  These are evaluation settings, not changes to live-turn policy. Check them
  against the selected Modal SDK version. See [scaling](https://modal.com/docs/guide/scale).
- Set a workspace usage cap no greater than the applicable credit allowance
  (normally $30 for an otherwise unused cycle), account for usage already incurred,
  and set the out-of-pocket spend limit to $0. Workspace budgets count usage
  before credits; spend limits count charges after credits. Environment budgets
  require a higher plan and are not assumed here. Verify the effective controls
  before starting; do not enable paid overage. See [budgets](https://modal.com/docs/guide/budgets).
- The runner reserves a bounded run allowance, checks remaining time/cost before
  dispatch, and stops before the cap with a margin for startup, idle time and
  delayed usage reporting. A timed-out HTTP client does not prove GPU work stopped:
  bound server execution, reconcile request identity, and stop the deployment
  explicitly after the batch. Record final usage and termination.
- Cache pinned weights deliberately to avoid repeated downloads; include cache
  storage in cost accounting. Persist results after each case so interruption
  does not require restarting the whole evaluation. No blind automatic retry
  after uncertain completion.

## Hosting architecture and implementation gates

M4 owns the runner, evaluator adapter and deployment source under `evaluation/`;
M2 reviews the isolated GPU environment and cost/lifecycle controls. The chosen
architecture is an authenticated Modal GPU Web Function wrapping the scorer,
called by an evaluation-owned HTTPX client. The API/worker and WPF never call it
during a student turn. It must not receive database or production-provider keys.

Protect the Web Function using Modal proxy authentication, explicitly enabled
with `requires_proxy_auth=True`. Keep tokens in the approved secret mechanism,
not URLs, source, notebooks, fixtures or logs. Reject unauthenticated traffic
before GPU allocation. See [proxy authentication](https://modal.com/docs/guide/webhook-proxy-auth).

The existing shared `httpx` pin is sufficient for the caller. Modal's deployment
SDK, torch, transformers and any selected inference engine belong to a separately
pinned evaluator environment/image, never the shared API/worker manifest/lock.
Ragas remains blocked; use repository-owned Ragas-style scripts. Do not introduce
the full LangChain framework or an OpenAI SDK to call an HTTP endpoint.

Before deployment, record exact Modal SDK, Python/CUDA, inference library versions,
container digest and model/tokenizer commit revisions. These GPU-environment
pins are pending compatibility review, not permission to use floating versions.
Keep the API/worker Python baseline unchanged. Start with unquantized BF16
weights on A100; estimated weight storage is about 14 GB for 7B parameters,
with additional runtime/KV-cache memory. Fit and throughput must be measured.
Quantization is a separately calibrated alternative, not the default shortcut.

Start with one case at a time and a conservative 4,096-token total sequence
ceiling, reserving up to 512 generated tokens within that total. These are pilot
caps, not advertised checkpoint maxima. Validate tokenization/template and
measured memory before increasing batching/context. Reject oversized input or
prepare a reviewed shorter case; never silently truncate the reference/rubric.
Treat truncated output as unscored. No endpoint URL, image pin or running
deployment is claimed by this documentation.

## Evaluation quality and scoring workflow

1. Collect permitted, versioned synthetic/public cases and already-produced
   Coordinator/Tutor outputs. Include sufficient evidence, missing axes,
   unsupported but plausible answers, incorrect units, alternative correct
   reasoning, assisted attempts and appropriate abstention. Keep private student
   records and credentials out of hosted evaluation fixtures.
2. Run deterministic reference/value/access/replay checks first. M3 reviews
   original media; M5 performs keyboard/NVDA, STOP and return tests. Prometheus-2
   is a text judge and cannot validate pixels, playback or accessibility.
3. M4 prepares a source-checked reference answer and one clearly anchored
   1–5 rubric per dimension: source support, question relevance, factual
   correctness and teaching usefulness. Use the checkpoint's documented absolute
   grading instructions and Mistral conversation template. Parse feedback plus
   the terminal `[RESULT]` integer strictly. Missing/duplicate/out-of-range
   scores and malformed/truncated responses are errors, not passes.
   [Prompt format](https://huggingface.co/prometheus-eval/prometheus-7b-v2.0#prompt-format)
4. Missing reference answers mean reference-based scoring is pending human
   labelling; do not invent a gold answer using the candidate being judged.
   Keep candidate text and evidence untrusted. Test rubric injection and answers
   asking the judge to assign a high score. Store concise evidence-based feedback,
   not private chain of thought.
5. Calibrate on at least ten human-labelled development cases before accepting
   aggregate scores. Record per-criterion exact/within-one agreement, absolute
   score error and human disagreements, especially unsupported answers receiving
   high scores. Freeze a separate held-out set before tuning. Ten cases are a
   pilot, not proof of statistical reliability; report sample size and coverage.
   Human reviewers adjudicate errors; do not invent a universal passing threshold.
6. For pairwise comparisons, use the documented separate format, anonymize model
   names, swap A/B ordering and flag inconsistent preferences for human review.
   Keep absolute scores and pairwise preferences distinct.
7. Run custom Ragas-style retrieval/answer metrics without installing Ragas.
   Relevant-evidence labels are required for context precision/recall. Missing
   labels produce not-evaluated results. Ordinal Prometheus scores are not
   Ragas-equivalent metric values or probabilities of correctness.
8. Persist case/source/answer hashes, producer model, evaluator checkpoint and
   tokenizer revisions, host/GPU/dtype, library/image versions, rubric/template
   versions, seed/generation settings, timestamps, elapsed time, cost, parsed
   score, feedback, errors and human adjudication. Use deterministic decoding
   where supported, without claiming bitwise reproducibility across hardware.
   Cache identity includes all inputs/configuration; label reused results.
9. Stop on credit exhaustion or persistent hosting failure; preserve completed
   cases and report remaining cases unscored. Continue deterministic/human checks,
   but mark the requested Prometheus evaluation milestone incomplete. No silent
   Gemini/GLM replacement and no paid overage.

The scaffold in `evaluation/scripts/interfaces.py` is not an implemented runner.
Keep ordinal scores, provenance and skipped/error outcomes in evaluation-owned
structures; do not coerce them into the existing boolean `EvaluationResult`
without an explicit reviewed mapping. Evaluation cannot mutate student history.

## Alternative hosts

| Host | Planned use and limitations |
|---|---|
| Modal — preferred | Authenticated, scale-to-zero A100 40 GB scorer with bounded $30-credit usage. Verify account eligibility and actual balance. |
| Lightning AI — alternative | Run the same pinned scorer in a private Studio/job; confirm available GPU, rate, credits, storage and auto-sleep before selecting. Use a protected endpoint only if account features permit; otherwise export batch results. |
| Kaggle — batch fallback | Run a private notebook over versioned cases, checkpoint results and export artifacts. Do not treat it as an always-on REST host or add a public tunnel. |

[Kaggle notebook documentation](https://www.kaggle.com/docs/notebooks) lists
12-hour GPU sessions and P100/T4 options; it does not promise an A100.
[GPU quota guidance](https://www.kaggle.com/docs/efficient-gpu-usage) describes
a variable weekly allowance. A 14-hour workload needs resumable batches.
A 16 GB device leaves little space beyond BF16 weights; dual T4 memory does
not pool automatically. A compatible sharding/precision configuration requires
separate validation and calibration, especially if quantized.

[Lightning billing](https://lightning.ai/docs/overview/faq/billing) describes
15 free credits topped up monthly, with compute and storage consuming credits.
Those credits do not promise a fixed number of A100 hours. Use
[Studio stop/auto-sleep controls](https://lightning.ai/docs/overview/ai-studio/start-and-stop-studio)
and verify current account GPU access; this plan does not select an upgraded tier.
Switching hosts is a recorded operator decision preserving the same checkpoint,
rubrics, cases and provenance; altered precision requires recalibration.

## GLM-5.2 clarification

The user's screenshot identifies **OpenRouter**, not Z.ai direct or NVIDIA.
The [OpenRouter free-model page](https://openrouter.ai/z-ai/glm-5.2:free)
confirms zero token price and lists 32,768 context/maximum completion tokens for
this endpoint, while the general model description mentions 1M context. The
endpoint limits govern; input and output must fit its actual combined allowance.
It also lists no `tools` or enforced `response_format` support. This corrects
the earlier unresolved-provider note; direct Z.ai pricing did not refute this offer.

Free API access is rate-limited; check current
[OpenRouter limits](https://openrouter.ai/docs/api-reference/limits) and account
availability before any comparison. No live call was made. Keep GLM as an optional
future comparison candidate: it is not the requested Prometheus checkpoint, and
this endpoint's tool limitations argue against a live Coordinator replacement.
No OpenRouter dependency, account setup or automatic model fallback is introduced.

## Member responsibilities and completion

| Owner | Required work |
|---|---|
| M1 | Retain live models/budgets; keep evaluator outside routing, boot/readiness and student turns; supply sanitized traces and review isolation with M4. |
| M2 | Review isolated Modal image/pins, credit cap, authentication and stop/resume controls with M4; provide source/version/relevance fixtures. Keep AWS GPUs and evaluator SDKs out of production. |
| M3 | Supply original-media checked labels/values/timestamps, unreadable variants and source-checked references; preserve Twelve Labs roles. |
| M4 | Own Prometheus-2 runner, Modal deployment source/HTTP adapter, rubric parser, calibration, result persistence and cost report. Document Lightning/Kaggle portability. |
| M5 | Verify accessible errors, STOP, reconnect and exact return; demonstrate evaluator outage has no student-path impact; no judge credentials or controls in WPF. |

Use the [updated member prompts](../team/prompts/README.md). Finish local fixture
checks and deployment preparation independently; only account/deployment/live
checks await execution authorization. Acceptance of hosted evaluation requires an
authorized authenticated smoke run, calibration report, measured cost and verified
shutdown. Documentation alone does not satisfy those gates.

This request updates documents only. It selects Modal hosting and the evaluator
architecture, but does not deploy, install dependencies, download weights, enter
payment details, spend credits or call providers. No application code, runtime
prompt, manifest, lock or public schema changes are made here.
