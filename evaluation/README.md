# Offline evaluation

Follow the [model and evaluation plan](../docs/architecture/model-evaluation-plan.md)
and [M4 prompt](../docs/team/prompts/M4.md).

Selected primary model judge: `prometheus-eval/prometheus-7b-v2.0` hosted on Modal
using one A100 40 GB. Retain deterministic/source checks and human calibration.
Lightning AI is an alternative scorer host; Kaggle is a resumable notebook-batch
alternative. No AWS GPU is needed. Gemini/GLM are not automatic replacements.

M4 owns deployment source, authenticated HTTPX adapter, runner and reports here;
M2 reviews the isolated GPU environment and lifecycle/cost controls. Pin model,
tokenizer, image and dependencies before deployment. Keep Modal SDK, torch,
transformers, vLLM/prometheus-eval and Ragas out of the shared API/worker lock.
No student request or startup depends on this external offline scorer.

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
