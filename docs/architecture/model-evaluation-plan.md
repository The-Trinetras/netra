# Model and evaluation plan

Decision date: 17 September 2026. This updates the plan at the user's request;
it does not claim deployed adapters, account access or completed evaluations.
It supersedes the earlier AWS GPU/Prometheus-2 evaluation requirement. Runtime
dependencies, wire contracts, ownership and the 4/6/20 live-turn budget remain
governed by their existing authorities.

## Selected configuration

| Responsibility | Selected plan | Reason |
|---|---|---|
| Coordinator / M1 | Keep Gemini `gemini-3.8-flash` | Existing configured model and SDK; provider lists free-tier API input/output. |
| Tutor / M4 | Keep Groq `openai/gpt-oss-120b` | Existing configured model and SDK; Groq lists this model in its free-plan limits. |
| Required evaluation / M4 with all owners | Deterministic checks, original-source review and human teaching rubrics | Runs without a GPU or judge API; remains the acceptance baseline. |
| Optional offline judge / M4 | Gemini `gemini-3.8-flash`, through a separate evaluation adapter using the existing `google-genai` pin | No new provider, SDK or GPU; cross-model review of Groq Tutor output. This is a planning choice, not a measured quality winner. |
| Prometheus-2 7B | Deferred optional comparison using `prometheus-eval/prometheus-7b-v2.0` | No AWS GPU prerequisite; revisit only when suitable hardware and execution are separately authorized. |
| GLM-5.2 | Candidate only; no replacement selected | The supplied free endpoint has not been verified. Direct Z.ai API pricing is paid. |

This is the best fit for the current budget and integration constraints, not a
claim that Gemini/Groq outperform GLM on Netra tasks. Keep named model IDs and
endpoint configuration separate from SDK versions. Other parsing, embedding,
video and speech providers are unchanged; this decision does not make the whole
application free. Do not replace an embedding model or rebuild indexes here.

## What was verified

Public documentation was checked on the decision date; no credentials were read
and no inference/account calls were made. Account eligibility, region, current
quota and adapter behaviour still need an explicitly authorized smoke test.

- Z.ai lists GLM-5.2 at $1.40 input and $4.40 output per million tokens. Its model
  page advertises 1M context and 128K maximum output, rather than the supplied
  32,768/32,768 description. A third-party host can impose different limits.
  See [Z.ai pricing](https://docs.z.ai/guides/overview/pricing) and
  [GLM-5.2 documentation](https://docs.z.ai/guides/llm/glm-5.2).
- NVIDIA's specific GLM-5.2 page labels the free endpoint deprecated, although
  its model catalogue still advertises a free endpoint. Do not rely on the
  catalogue badge as account access evidence. See the
  [NVIDIA model page](https://build.nvidia.com/z-ai/glm-5.2?nim=self-hosted).
- Google lists free-tier input/output for `gemini-3.8-flash`. Free-tier content
  can be used to improve Google's products. Use only team-authored synthetic or
  public permitted fixtures for this offline evaluation plan; do not upload
  private student history, answers or source documents to the free judge.
  See [Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing).
- Groq lists `openai/gpt-oss-120b` with a 131,072-token context and 65,536 maximum
  completion tokens. Its listed free limits are 30 RPM, 1,000 RPD, 8,000 TPM and
  200,000 TPD; organization-specific limits can differ. Context capacity does
  not override token-rate limits. See [models](https://console.groq.com/docs/models)
  and [rate limits](https://console.groq.com/docs/rate-limits).
- The user reports that this AWS Free Plan account cannot launch `g5.xlarge`.
  Treat that as the project's deployment constraint, not merely a GPU quota
  request to retry or a claim about every promotional-credit account.

The supplied Google share link could not be resolved during this review. A
direct GLM host/model URL, actual free allowance, expiry, privacy terms and
account availability are required before reconsidering it. Free weights or a
free chat interface do not establish a free hosted API. Context/output maxima
also do not mean both can be consumed simultaneously in one request.

## Evaluation workflow without a GPU

1. M1–M5 provide versioned, permitted fixtures and recorded observable outcomes.
   Start with the AgentSpec chapter/lecture case, sufficient-first evidence,
   missing axes, unreadable evidence, incorrect units and assisted answers.
   Never export private chain of thought or credentials.
2. M4 runs deterministic evidence/reference/value checks, replay/cancellation
   checks and source-review rubrics first. M3 checks original media; M5 performs
   keyboard/NVDA and playback tests. A text judge cannot replace those checks.
3. Implement Ragas-style metrics in repository evaluation scripts, using metric
   definitions as references only. The Ragas package remains blocked by the
   [dependency baseline](runtime-baseline.md#evaluation-dependencies). Do not
   describe custom metrics as an executed Ragas benchmark or equivalent scores.
   Precision/recall require labelled relevant evidence; missing labels mean
   not evaluated, not a fabricated value. No new embedding model is selected.
4. Add optional Gemini rubric scoring of already-produced answers in `evaluation/`.
   The adapter receives task, bounded source excerpts, candidate answer, reference
   when available and a versioned rubric. Validate structured results and cited
   evidence; use short evidence-based justifications, never private reasoning.
   Treat instructions within candidate answers or evidence as untrusted data.
5. Calibrate with ten human-labelled development cases, including unsupported
   claims and plausible wrong answers. Record per-criterion disagreements and
   human adjudication. Freeze a separate held-out set before tuning. Ten cases
   are a starting check, not evidence of statistical reliability. Same-model
   review of Coordinator output is explicitly labelled self-evaluation and is
   excluded from independent-judge claims; human review remains decisive.
6. Run sequentially by default, outside live answer execution, with explicit
   request/token/output/time ceilings per run. Use account limits, bounded
   backoff and Retry-After where supplied; stop on exhausted free quota. Never
   enable paid fallback, add accounts to evade limits or silently switch models.
   Schedule outside demonstrations because Coordinator and evaluator can share
   Gemini project quota. If unavailable, finish deterministic/human review and
   report model scoring as skipped with its reason; never report a pass or zero
   score for an unevaluated case.
7. Record commit, fixture/source versions, producer provider/model, judge model
   and endpoint, rubric/prompt versions, generation settings, token usage,
   latency, cache/replay status, errors, skips and human disagreement. Keep
   artefacts read-only with respect to student/session/history stores. Include
   producer, source, answer, rubric and judge configuration in any result-cache
   identity. A cache hit is a replayed result, not a fresh independent sample.

The existing `evaluation/scripts/interfaces.py` is an interface scaffold, not
a runner. M4 must inspect it and keep run provenance/skipped/error reporting in
evaluation-owned structures; do not coerce missing scores into a passing
`EvaluationResult` or alter public contracts for offline metadata.

## Prometheus-2 and hardware

Remove AWS GPU provisioning, quota increases and GPU spend from the required
build/demo path. Keep the API/worker on the existing EC2/Compose plan. No GPU
container, NIM service or local model server is added to production.

An already accessible A100 40 GB can be considered for a later Prometheus-2 batch,
but it is not promised free or proven fastest for this workload. A separately
approved external rental also costs money; free notebooks do not guarantee an
A100 or stable availability. Benchmark fit, sequence length, batch size, startup
and total elapsed time before selecting hardware. Pin checkpoint revision,
container digest and inference configuration; review the
[Prometheus-2 model card](https://huggingface.co/prometheus-eval/prometheus-7b-v2.0).
Keep torch/transformers/vLLM/prometheus-eval outside the API/worker dependencies.
Downloading weights, renting compute or calling a judge requires separate
execution authorization. Prometheus-2 absence must not block the demo.

## Member responsibilities and completion

| Owner | Required change to their work |
|---|---|
| M1 | Retain Coordinator pin/budgets; separate evaluation configuration and quota from turn execution; supply sanitized evidence-gap/cancellation traces and explicit provider-unavailable handling. Coordinate evaluator SDK access with M4 without calling the Coordinator loop. |
| M2 | Remove GPU assumptions from infrastructure/dependency planning; provide versioned permitted retrieval fixtures and relevance labels; preserve embedding dimensions, authorization and canonical storage. |
| M3 | Supply original-media checked graph/table/equation/timestamp cases with uncertain/transcript-only variants; keep Twelve Labs roles and playback/analysis gates. |
| M4 | Retain Groq Tutor; own offline runner, Gemini rubric adapter, custom metrics, calibration, quota/skip handling and reproducible reports; defer Prometheus-2 and keep Ragas uninstalled. |
| M5 | Verify accessible quota/unavailable states, keyboard/text reduced modes, STOP and return; do not add judge controls, GPU setup or API keys to the client. |

See the updated [complete member prompts](../team/prompts/README.md). This
documentation task changes no source, manifest, lock, runtime prompt or wire
schema. It authorizes the revised plan; invoking a member prompt authorizes that
member's implementation under the existing review controls. Package installs,
provider calls, hardware provisioning and deployment remain separate actions.
