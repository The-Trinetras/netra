"""Judge transport: the authenticated HTTP boundary to the Prometheus scorer.

model-evaluation-plan.md "Hosting architecture": an authenticated Modal GPU
web endpoint wrapping the scorer, called by an evaluation-owned HTTPX
client. Modal proxy auth (``requires_proxy_auth=True``) rejects requests
without the ``Modal-Key`` / ``Modal-Secret`` headers with 401 before the
function runs, so unauthenticated traffic never allocates a GPU.

Endpoint contract (implemented by evaluation/deploy/prometheus_modal.py):

- ``POST /score`` with {request_id, prompt, max_new_tokens,
  max_total_tokens, temperature, seed} returns {request_id, output,
  finish_reason, prompt_tokens, completion_tokens, gpu_seconds}.
  The server records each request_id's result so it can be reconciled.
- ``GET /result/{request_id}`` returns the same body if that request
  completed, 404 if the server never saw it, 409 if it is still running.

Error mapping never forwards response bodies: only the enumerated
ErrorCode leaves this module. Credentials come from the environment
variables named below, are never logged, and never appear in URLs.
No network call happens unless a runner is explicitly started live.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

from eval_results import ErrorCode

ENV_JUDGE_URL = "NETRA_EVAL_JUDGE_URL"
ENV_MODAL_TOKEN_ID = "NETRA_EVAL_MODAL_PROXY_TOKEN_ID"
ENV_MODAL_TOKEN_SECRET = "NETRA_EVAL_MODAL_PROXY_TOKEN_SECRET"


@dataclass(frozen=True)
class JudgeRequest:
    request_id: str
    prompt: str
    max_new_tokens: int
    max_total_tokens: int
    temperature: float
    seed: Optional[int]

    def as_json(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "prompt": self.prompt,
            "max_new_tokens": self.max_new_tokens,
            "max_total_tokens": self.max_total_tokens,
            "temperature": self.temperature,
            "seed": self.seed,
        }


@dataclass(frozen=True)
class JudgeReply:
    request_id: str
    output: str
    finish_reason: str
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    gpu_seconds: Optional[float] = None
    cold_start_seconds: float = 0.0


class JudgeCallError(Exception):
    """A failed judge call, reduced to a safe code.

    ``stop_run`` marks failures where continuing would waste allowance or
    hammer the host: authentication, credit exhaustion and rate limiting.
    ``uncertain`` marks failures after which the server may still have
    completed the work; those must be reconciled before any retry.
    """

    def __init__(self, code: ErrorCode, *, stop_run: bool = False, uncertain: bool = False) -> None:
        self.code = code
        self.stop_run = stop_run
        self.uncertain = uncertain
        super().__init__(code.value)


class LookupUnsupportedError(Exception):
    """The transport cannot reconcile an uncertain request."""


class StillRunningError(Exception):
    """The server reports the request is still executing."""


class JudgeTransport(Protocol):
    async def score(self, request: JudgeRequest) -> JudgeReply:
        """Raise JudgeCallError on failure."""
        ...

    async def lookup(self, request_id: str) -> Optional[JudgeReply]:
        """Return the completed reply, None if never seen, raise
        StillRunningError if running, LookupUnsupportedError if the
        transport cannot reconcile."""
        ...


def error_for_status(status: int, body: Optional[dict[str, Any]]) -> JudgeCallError:
    """Map an HTTP failure to a safe code. The body is inspected only for a
    known ``error`` token, never forwarded."""

    token = (body or {}).get("error") if isinstance(body, dict) else None
    if status in (401, 403):
        return JudgeCallError(ErrorCode.AUTH_FAILED, stop_run=True)
    if status == 402 or token == "credit_exhausted":
        return JudgeCallError(ErrorCode.CREDIT_EXHAUSTED, stop_run=True)
    if status == 429:
        return JudgeCallError(ErrorCode.RATE_LIMITED, stop_run=True)
    if status == 413 or token == "oversized_input":
        return JudgeCallError(ErrorCode.OVERSIZED_INPUT)
    if token == "out_of_memory":
        return JudgeCallError(ErrorCode.OUT_OF_MEMORY)
    if status in (409, 502, 504):
        # 409: this request_id is already running server-side. 502/504: the
        # gateway gave up while the function may still have run. Either
        # way the outcome is unknown and must be reconciled, not re-sent.
        return JudgeCallError(ErrorCode.TIMEOUT_UNCERTAIN, uncertain=True)
    if status >= 500:
        return JudgeCallError(ErrorCode.SERVER_ERROR)
    return JudgeCallError(ErrorCode.PROTOCOL_ERROR)


def reply_from_json(body: Any, expected_request_id: str) -> JudgeReply:
    if not isinstance(body, dict):
        raise JudgeCallError(ErrorCode.PROTOCOL_ERROR)
    try:
        reply = JudgeReply(
            request_id=str(body["request_id"]),
            output=str(body["output"]),
            finish_reason=str(body["finish_reason"]),
            prompt_tokens=body.get("prompt_tokens"),
            completion_tokens=body.get("completion_tokens"),
            gpu_seconds=body.get("gpu_seconds"),
            cold_start_seconds=float(body.get("cold_start_seconds") or 0.0),
        )
    except KeyError as missing:
        raise JudgeCallError(ErrorCode.PROTOCOL_ERROR) from missing
    if reply.request_id != expected_request_id:
        raise JudgeCallError(ErrorCode.PROTOCOL_ERROR)
    return reply


@dataclass
class HttpxJudgeTransport:
    """HTTPX client for the Modal endpoint. Uses the shared httpx pin only.

    ``client`` may be injected (an ``httpx.AsyncClient``, including one
    built on ``httpx.MockTransport`` in tests). Otherwise one is created
    with the proxy-auth headers and an explicit timeout.
    """

    base_url: str
    token_id: str = field(repr=False)
    token_secret: str = field(repr=False)
    timeout_seconds: float = 120.0
    client: Any = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not self.base_url.startswith("https://"):
            raise ValueError("the judge endpoint must be https")
        if "?" in self.base_url or "@" in self.base_url:
            raise ValueError("credentials and query strings never go in the judge URL")
        if self.client is None:
            import httpx  # shared pin; imported only when a live transport is built

            self.client = httpx.AsyncClient(
                base_url=self.base_url.rstrip("/"),
                headers={"Modal-Key": self.token_id, "Modal-Secret": self.token_secret},
                timeout=httpx.Timeout(self.timeout_seconds),
                follow_redirects=False,
            )

    async def score(self, request: JudgeRequest) -> JudgeReply:
        import httpx

        try:
            response = await self.client.post("/score", json=request.as_json())
        except httpx.TimeoutException as timeout:
            raise JudgeCallError(ErrorCode.TIMEOUT_UNCERTAIN, uncertain=True) from timeout
        except httpx.TransportError as transport:
            # The request may or may not have reached the server.
            raise JudgeCallError(ErrorCode.TIMEOUT_UNCERTAIN, uncertain=True) from transport
        body = _json_or_none(response)
        if response.status_code != 200:
            raise error_for_status(response.status_code, body)
        return reply_from_json(body, request.request_id)

    async def lookup(self, request_id: str) -> Optional[JudgeReply]:
        import httpx

        try:
            response = await self.client.get(f"/result/{request_id}")
        except httpx.HTTPError as failure:
            raise LookupUnsupportedError("the judge result endpoint is unreachable") from failure
        if response.status_code == 404:
            return None
        if response.status_code == 409:
            raise StillRunningError(request_id)
        if response.status_code != 200:
            raise LookupUnsupportedError(f"result lookup returned HTTP {response.status_code}")
        return reply_from_json(_json_or_none(response), request_id)

    async def aclose(self) -> None:
        await self.client.aclose()


def _json_or_none(response: Any) -> Optional[dict[str, Any]]:
    try:
        body = response.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


@dataclass
class RunAllowance:
    """Bounded judge allowance for one run, checked before every dispatch.

    model-evaluation-plan.md: "The runner reserves a bounded run
    allowance, checks remaining time/cost before dispatch, and stops
    before the cap with a margin for startup, idle time and delayed usage
    reporting." Spend is tracked in GPU seconds as reported by the
    endpoint, or client wall-clock time when it reports none (an upper
    bound for this one-in-flight client, not a billing figure). Actual
    billed usage must still be read from the Modal account afterwards.
    """

    max_gpu_seconds: float
    margin_seconds: float
    per_call_estimate_seconds: float
    spent_seconds: float = 0.0
    calls: int = 0
    cold_start_seconds: float = 0.0
    cold_starts: int = 0
    per_cold_start_estimate_seconds: float = 0.0

    def can_dispatch(self) -> bool:
        """Reserve a possible cold start as well as the call itself.

        OPT-8: with ``min_containers=0`` and a 60 s scaledown window, any gap
        between judgements can drop the container, and reloading 7B weights
        onto the A100 is billed. Dispatching on a budget that assumed a warm
        container is how a run overruns its cap.
        """

        needed = self.per_call_estimate_seconds + self.per_cold_start_estimate_seconds + self.margin_seconds
        return self.spent_seconds + needed <= self.max_gpu_seconds

    def record(
        self,
        reply_seconds: Optional[float],
        wall_seconds: float,
        cold_start_seconds: float = 0.0,
    ) -> None:
        """Record one call's spend, including any cold start it paid for.

        ``gpu_seconds`` is measured inside the request handler, so it excludes
        the container load that preceded it; that load is billed and is added
        here. A failed call reports no cold start, and wall-clock time already
        covers whatever it spent.
        """

        self.calls += 1
        self.spent_seconds += reply_seconds if reply_seconds is not None else wall_seconds
        if cold_start_seconds:
            self.cold_starts += 1
            self.cold_start_seconds += cold_start_seconds
            self.spent_seconds += cold_start_seconds

    def estimated_cost_usd(self, rate_per_second: float = 0.000583) -> float:
        """A100-40GB GPU-only rate from the plan; excludes CPU/RAM/idle/storage."""

        return round(self.spent_seconds * rate_per_second, 4)
