"""Prometheus-2 7B scorer on Modal — deployment source (NOT deployed).

model-evaluation-plan.md "Hosting architecture and implementation gates".
This file is deployment preparation only. It is not imported by tests or by
the Netra API/worker, and it never runs in a student turn. Deploying it
needs an explicit execution authorization (account, payment method entered
by the user, credit cap, spend limit $0) and the pending pins below.

Fail-closed pins: ``modal deploy`` imports this module, and the import
raises while any value in PINS is still ``PENDING_M2_REVIEW``. Nothing
floating ("latest", unpinned pip names) is allowed.

Controls implemented here (check against the pinned Modal SDK version):
- ``requires_proxy_auth=True``: requests without Modal-Key/Modal-Secret
  get 401 before the function runs, so no GPU is allocated for them.
- one A100-40GB, ``max_containers=1``, ``min_containers=0``,
  ``buffer_containers=0``, ``scaledown_window=60``, one input at a time,
  and a bounded per-call ``timeout``. No warm pool or keepalive.
- The prompt is tokenized with the pinned tokenizer; prompt + max_new_tokens
  above max_total_tokens returns 413 ``oversized_input`` — never silently
  truncated. Greedy decoding when temperature is 0.
- Every request_id's state is written to a Modal Dict ("running", then the
  reply), so a client that timed out can reconcile via GET /result/{id}
  instead of re-sending (and paying for) the same judgement.
- The HTTP surface is a CPU function, not the A100 class (OPT-7). GET
  /result only reads that Dict, so reconciling never allocates a GPU; only
  POST /score calls the GPU class, and that call is what wakes it. Both
  routes keep one URL, so NETRA_EVAL_JUDGE_URL is unchanged.
- Each reply reports cold_start_seconds alongside gpu_seconds (OPT-8).
  gpu_seconds is measured inside the handler and so excludes the container
  load before it; that load is billed, and the runner's allowance charges
  and reserves for it rather than discovering it on the invoice.
- CUDA OOM returns 503 ``out_of_memory``; nothing else from the exception
  leaves the container.

After a batch the operator stops the app explicitly (``modal app stop``)
and records billed usage from the Modal dashboard; scale-to-zero alone is
not treated as proof of shutdown.

Portability: the same scoring function (``score_prompt``) can run in a
private Lightning AI Studio job or a Kaggle notebook batch over the same
cases, checkpoint and dtype. A T4/P100 16 GB device cannot hold BF16
weights plus KV cache comfortably and has no BF16 on P100; any change of
precision/quantization is a new judge configuration that must be
recalibrated, never a silent fallback.
"""

from __future__ import annotations

PENDING = "PENDING_M2_REVIEW"

PINS = {
    # Observed Hugging Face repo head for prometheus-eval/prometheus-7b-v2.0
    # on 2026-09-18 (model card check). Confirm it contains the reviewed
    # weights and tokenizer before use.
    "model_revision": "66ffb1fc20beebfb60a3964a957d9011723116c5",
    "tokenizer_revision": "66ffb1fc20beebfb60a3964a957d9011723116c5",
    "modal_sdk": PENDING,
    "python": PENDING,
    "cuda_base_image": PENDING,
    "torch": PENDING,
    "transformers": PENDING,
    "accelerate": PENDING,
    "fastapi": PENDING,
}

MODEL_ID = "prometheus-eval/prometheus-7b-v2.0"
APP_NAME = "netra-prometheus-judge"
RESULTS_DICT = "netra-prometheus-judge-results"
WEIGHTS_VOLUME = "netra-prometheus-weights"
PER_CALL_TIMEOUT_SECONDS = 600
DTYPE = "bfloat16"


def assert_pins_reviewed() -> None:
    pending = sorted(name for name, value in PINS.items() if value == PENDING)
    if pending:
        raise RuntimeError(
            "Prometheus scorer deployment is blocked: unreviewed pins " + ", ".join(pending)
            + ". Record exact versions/digests with M2 review first."
        )


def check_budget(prompt_tokens: int, max_new_tokens: int, max_total_tokens: int) -> bool:
    return prompt_tokens + max_new_tokens <= max_total_tokens


ABANDON_AFTER_SECONDS = PER_CALL_TIMEOUT_SECONDS + 60


def is_abandoned(entry: dict, now: float) -> bool:
    """A "running" entry older than the per-call timeout cannot still be
    executing (Modal ends the call at the timeout), so it never completed."""

    return entry.get("state") == "running" and now - float(entry.get("started_at", 0)) > ABANDON_AFTER_SECONDS


assert_pins_reviewed()

# Everything below runs only once every pin is reviewed.
import modal  # noqa: E402

image = (
    modal.Image.from_registry(PINS["cuda_base_image"], add_python=PINS["python"])
    .pip_install(
        f"torch=={PINS['torch']}",
        f"transformers=={PINS['transformers']}",
        f"accelerate=={PINS['accelerate']}",
        f"fastapi=={PINS['fastapi']}",
    )
    .env({"HF_HUB_OFFLINE": "0", "TOKENIZERS_PARALLELISM": "false"})
)
app = modal.App(APP_NAME, image=image)
weights = modal.Volume.from_name(WEIGHTS_VOLUME, create_if_missing=True)
results = modal.Dict.from_name(RESULTS_DICT, create_if_missing=True)


@app.cls(
    gpu="A100-40GB",
    max_containers=1,
    min_containers=0,
    buffer_containers=0,
    scaledown_window=60,
    timeout=PER_CALL_TIMEOUT_SECONDS,
    volumes={"/weights": weights},
)
@modal.concurrent(max_inputs=1)
class PrometheusJudge:
    @modal.enter()
    def load(self) -> None:
        import time

        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        started = time.monotonic()
        self.torch = torch
        self.tokenizer = AutoTokenizer.from_pretrained(
            MODEL_ID, revision=PINS["tokenizer_revision"], cache_dir="/weights"
        )
        self.model = AutoModelForCausalLM.from_pretrained(
            MODEL_ID, revision=PINS["model_revision"], cache_dir="/weights",
            torch_dtype=getattr(torch, DTYPE), device_map="cuda",
        )
        self.model.eval()
        weights.commit()
        # OPT-8: pulling 7B weights onto the A100 is billed GPU time, but it
        # happens before any handler starts timing, so gpu_seconds alone
        # under-reports every cold start. Charge it to the first call that
        # uses this container; later calls on the same container add nothing.
        self._cold_start_seconds = round(time.monotonic() - started, 3)

    def _take_cold_start(self) -> float:
        charged = getattr(self, "_cold_start_seconds", 0.0)
        self._cold_start_seconds = 0.0
        return charged

    @modal.method()
    def score_prompt(self, prompt: str, max_new_tokens: int, max_total_tokens: int, temperature: float, seed) -> dict:
        """Score one prompt on the GPU and return a tagged result.

        Returns rather than raises, because the caller is now a separate CPU
        function (OPT-7): a torch exception type cannot cross that boundary.
        """

        try:
            return {**self._score(prompt, max_new_tokens, max_total_tokens, temperature, seed),
                    "cold_start_seconds": self._take_cold_start()}
        except self.torch.cuda.OutOfMemoryError:
            return {"status": "out_of_memory", "cold_start_seconds": self._take_cold_start()}

    def _score(self, prompt: str, max_new_tokens: int, max_total_tokens: int, temperature: float, seed) -> dict:
        encoded = self.tokenizer(prompt, return_tensors="pt", truncation=False)
        prompt_tokens = int(encoded["input_ids"].shape[1])
        if not check_budget(prompt_tokens, max_new_tokens, max_total_tokens):
            return {"status": "oversized_input", "prompt_tokens": prompt_tokens}
        if seed is not None:
            self.torch.manual_seed(int(seed))
        with self.torch.inference_mode():
            generated = self.model.generate(
                **{k: v.to("cuda") for k, v in encoded.items()},
                max_new_tokens=max_new_tokens,
                do_sample=temperature > 0,
                temperature=temperature if temperature > 0 else None,
                top_p=None,
                pad_token_id=self.tokenizer.eos_token_id,
            )
        new_tokens = generated[0, prompt_tokens:]
        finished = bool(len(new_tokens)) and int(new_tokens[-1]) == self.tokenizer.eos_token_id
        text = self.tokenizer.decode(new_tokens, skip_special_tokens=True)
        return {
            "status": "ok",
            "output": text,
            "finish_reason": "stop" if finished else "length",
            "prompt_tokens": prompt_tokens,
            "completion_tokens": int(len(new_tokens)),
        }


# OPT-7: the HTTP surface runs on a CPU container, not on the A100 class.
# GET /result only reads the results Dict, so a client reconciling a timed-out
# judgement never allocates a GPU; only POST /score calls the GPU class, and
# that call is what wakes it.
@app.function(cpu=1.0, timeout=PER_CALL_TIMEOUT_SECONDS + 60)
@modal.asgi_app(requires_proxy_auth=True)
def web():
    import time

    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    api = FastAPI()

    @api.post("/score")
    def score(body: dict):
        request_id = str(body["request_id"])
        previous = results.get(request_id)
        if previous is not None and previous.get("state") == "done":
            return previous["reply"]
        if previous is not None and not is_abandoned(previous, time.time()):
            return JSONResponse({"error": "in_progress"}, status_code=409)
        results[request_id] = {"state": "running", "started_at": time.time()}
        started = time.monotonic()
        outcome = PrometheusJudge().score_prompt.remote(
            body["prompt"], int(body["max_new_tokens"]), int(body["max_total_tokens"]),
            float(body["temperature"]), body.get("seed"),
        )
        status = outcome.get("status")
        if status == "out_of_memory":
            results.pop(request_id, None)
            return JSONResponse({"error": "out_of_memory"}, status_code=503)
        if status == "oversized_input":
            results.pop(request_id, None)
            return JSONResponse(
                {"error": "oversized_input", "prompt_tokens": outcome["prompt_tokens"]}, status_code=413
            )
        reply = {k: v for k, v in outcome.items() if k not in ("status", "cold_start_seconds")}
        reply = {
            "request_id": request_id,
            **reply,
            "gpu_seconds": round(time.monotonic() - started, 3),
            # OPT-8: billed separately from gpu_seconds so the runner's
            # allowance can count the container load it just paid for.
            "cold_start_seconds": outcome.get("cold_start_seconds", 0.0),
        }
        results[request_id] = {"state": "done", "reply": reply}
        return reply

    @api.get("/result/{request_id}")
    def result(request_id: str):
        entry = results.get(request_id)
        if entry is None or is_abandoned(entry, time.time()):
            return JSONResponse({"error": "unknown"}, status_code=404)
        if entry.get("state") != "done":
            return JSONResponse({"error": "in_progress"}, status_code=409)
        return entry["reply"]

    return api
