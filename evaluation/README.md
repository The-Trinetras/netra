# Offline evaluation

Follow the [model and evaluation plan](../docs/architecture/model-evaluation-plan.md)
and [Arize AX integration plan](../docs/architecture/arize-ax-integration.md), plus
the [M4 prompt](../docs/team/prompts/M4.md).

Selected primary model judge: `prometheus-eval/prometheus-7b-v2.0` hosted on Modal
using one A100 40 GB. Retain deterministic/source checks and human calibration.
Lightning AI is an alternative scorer host; Kaggle is a resumable notebook-batch
alternative. No AWS GPU is needed. Gemini/GLM are not automatic replacements.

M4 owns deployment source, authenticated HTTPX adapter, runner and reports here;
M2 reviews the isolated GPU environment and lifecycle/cost controls. Pin model,
tokenizer, image and dependencies before deployment. Keep Modal SDK, torch,
transformers, vLLM/prometheus-eval and Ragas out of the shared API/worker lock.
No student request or startup depends on this external offline scorer.

AX is the selected dataset/experiment/comparison destination. Netra's runner calls
the external judge and uploads results; AX-hosted custom-code evaluators are not
required. Alyx assists engineering investigation and reviewed evaluation design,
not product execution or unreviewed reference labelling. M1 owns shared tracing;
M4 owns the separate AX experiment adapter/environment and trace-to-case mappings.

The required workflow is: reviewed reference dataset, error analysis, versioned
evaluation names, calibrated rubrics, then judge execution and paired comparison.
Persist immutable dataset snapshots, producer outputs, per-case scores/errors and
all configuration/provenance before uploading. Reconcile uncertain uploads and
resume without rerunning Netra or the judge. Compare identical held-out cases,
references, rubric and judge settings; rescore both frozen output sets if the judge
changes. Show regressions, missing-case denominators and trace completeness, not
only an average. Preserve artifacts outside AX retention and canonical student state.
Complete reliability, calibration and recovery gates; a quick pilot is insufficient.
No high-volume evaluator platform or sampling is needed for this Free-tier workload.

Use the documented grading template, source-checked references, anchored 1–5
rubrics and strict parsing. Calibrate human-labelled cases before reporting
aggregate scores. Preserve per-case results and host/configuration provenance.
Credits exhausted or invalid output means unscored, not passed; hosted evaluation
remains incomplete until executed and checked. Test interruption/retry recovery,
authentication, scale-to-zero, credit cap and explicit shutdown. The $30 allowance
is about 14.3 GPU-only hours, with less usable time after other costs.

`scripts/interfaces.py` supplies existing typed evaluation interfaces; it is not
an implemented runner. No live scoring or new command is claimed here. M4 owns
runner/adapter work, explicit skipped/error results, versioned fixtures, calibration
and execution instructions. Use permitted synthetic/public fixtures and label replay.
This documentation change does not install packages, download weights, deploy or
spend credits. Live completion needs authenticated scoring, calibrated results,
measured total cost and verified shutdown under separate execution authorization.
AX completion additionally requires confirmed dataset/experiment ingestion, complete
linked traces and a reproducible before/after comparison. No AX client or runner
command is implemented by this documentation revision.

## Implemented offline tooling (M4 follow-up, `codex/m4-tutor-followup`)

Local tooling now exists; **no live scoring, upload, deployment or calibration has
been executed**, and every judge score in the test suites is scripted fixture data.

| Piece | File | State |
|---|---|---|
| Ordinal results with explicit not-applicable/missing/invalid/failed | `scripts/eval_results.py` | Implemented, fixture-tested |
| Prometheus-2 absolute/pairwise templates (model card @ `66ffb1f`, FastChat `mistral`) and strict `[RESULT]` parsing | `scripts/prometheus.py` | Implemented, fixture-tested |
| Dataset snapshots, content hashes, label provenance, held-out freeze gate | `scripts/eval_dataset.py` | Implemented; freeze refuses non-gold held-out cases |
| Append-only artifacts (manifest, frozen outputs, results, uploads) with resume | `scripts/eval_store.py` | Implemented, fixture-tested |
| Authenticated HTTPX judge transport, safe error mapping, run allowance | `scripts/judge_client.py` | Implemented; HTTPX path only substitute-run (see handoff) |
| Resumable judge runner with uncertain-completion reconciliation | `scripts/judge_runner.py` | Implemented, fixture-tested |
| AX upload with ambiguous-upload reconciliation and id mapping | `scripts/ax_upload.py` | Logic implemented; **AX SDK client pending a reviewed pin** (`PendingAxSdkClient` fails closed) |
| Calibration statistics, paired comparison, pairwise swap consistency | `scripts/calibration.py`, `scripts/comparison.py` | Implemented, fixture-tested |
| Ragas-style retrieval metrics (Ragas not installed) | `scripts/ragas_style.py` | Implemented; needs human relevance labels |
| Modal deployment source | `deploy/prometheus_modal.py` | Written, **not deployed**; import fails closed until GPU-environment pins are reviewed with M2 |
| Named rubrics `source_support_v1`, `factual_correctness_v1`, `question_relevance_v1`, `teaching_usefulness_v1` | `rubrics/*.json` | **Draft, uncalibrated, unreviewed** |
| Reference dataset `tutor-reference-v1` (11 synthetic Ohm's Law development cases) | `datasets/tutor_reference_v1.json` | **Draft**: references are suggestions, no held-out split, no human error analysis yet |

Commands (run from the repository root; artifacts go outside the repository):

```text
python evaluation/scripts/eval_cli.py validate evaluation/datasets/tutor_reference_v1.json
python evaluation/scripts/eval_cli.py init-run --artifacts <dir> --run-id <id> --dataset evaluation/datasets/tutor_reference_v1.json --split development --criteria source_support_v1,factual_correctness_v1 --producer <producer.json> --judge-config <judge.json>
python evaluation/scripts/eval_cli.py replay-fixtures --artifacts <dir> --run-id <id> --dataset evaluation/datasets/tutor_reference_v1.json
python evaluation/scripts/eval_cli.py status --artifacts <dir> --run-id <id>
python evaluation/scripts/eval_cli.py calibrate --artifacts <dir> --run-id <id> --labels <human_labels.json>
python evaluation/scripts/eval_cli.py compare --artifacts <dir> --dataset <dataset> --baseline <id> --candidate <id> --out <dir>
```

`judge --live` and `upload --live` are external actions requiring explicit execution
authorization; without `--live` they refuse. `judge` reads the endpoint URL and proxy
token only from `NETRA_EVAL_JUDGE_URL`, `NETRA_EVAL_MODAL_PROXY_TOKEN_ID` and
`NETRA_EVAL_MODAL_PROXY_TOKEN_SECRET`. After any live batch, stop the Modal app
explicitly and record billed usage from the account; the runner's cost figure is a
GPU-only estimate. `replay-fixtures` exercises the scorer on authored fixture text and
must never be reported as Netra output; real candidate outputs require the integrated
Tutor path (wave 6). Remaining gates are listed in `docs/team/handoffs/M4.md`.

## M2 retrieval fixtures and harness

M2 supplies retrieval fixtures and a retrieval-only benchmark; it does not own
the judge, rubrics or AX adapter above. `evaluation/scripts/retrieval_evaluation.py`,
`retrieval_experiments.py` and `hybrid_metrics.py` compute Precision@5,
Recall@5/10, MRR, nDCG@5, source-version/authorization correctness and latency
through the production lexical, semantic, RRF, BGE and PostgreSQL evidence
boundaries. `evaluation/manifests/retrieval_v1.json` pins every stage flag for
the E0–E5 matrix (E0 lexical, E1 semantic, E2 hybrid without RRF, E3 hybrid+RRF,
E4 +BGE, E5 metadata-filtered). E4/E5 report *blocked* without a real BGE model.

`evaluation/cases/netra_e3_real_golden_v1.jsonl` and
`netra_p3_answer_golden_v1.jsonl` reference one lecture PDF by immutable
source/version IDs and SHA-256. The PDF and its parsed text are deliberately
**not** committed (redistribution rights unconfirmed); they stay local under the
git-ignored `evaluation/fixtures/`. Without that file the real-golden runs are
skipped, not passed. Denied, deleted, stale and incompatible-embedding cases for
M4 are listed in `docs/team/handoffs/M2.md`.

Real runs need a designated database and provider configuration, and write
results under `evaluation/results/` (git-ignored, never overwritten implicitly):

```powershell
uv run --locked python evaluation/scripts/run_m2_evaluation.py --experiment E3
```
