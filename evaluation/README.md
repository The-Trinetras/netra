# Offline evaluation

Follow the [model and evaluation plan](../docs/architecture/model-evaluation-plan.md)
and [M4 prompt](../docs/team/prompts/M4.md).

Required: deterministic checks, original-source review and human rubrics.
Optional: Gemini `gemini-3.8-flash` rubric scoring through an evaluation adapter
using the already-declared `google-genai` SDK, outside student response execution.
Prometheus-2 is deferred; AWS GPU access is not a prerequisite. Do not install
Ragas, torch, transformers, vLLM or prometheus-eval into the shared runtime.

`scripts/interfaces.py` supplies existing typed evaluation interfaces; it is not
an implemented runner. No live scoring or new command is claimed here. M4 owns
runner/adapter work, explicit skipped/error results, versioned fixtures, calibration
and execution instructions. Use permitted synthetic/public fixtures, report replay
and self-evaluation, and retain human/source checks when free quota is unavailable.
