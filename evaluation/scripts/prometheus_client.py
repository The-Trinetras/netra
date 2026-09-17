"""Async, strict OpenAI-compatible client for offline Prometheus-2 grading."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

import httpx


class PrometheusEvaluationError(RuntimeError): pass
class PrometheusTransientError(PrometheusEvaluationError): pass
class PrometheusResponseError(PrometheusEvaluationError): pass

_RESULT_MARKER = re.compile(r"\[RESULT\]")
_COMPLETE_RESULT = re.compile(
    r"\A(?P<feedback>.*?)\[RESULT\]\s*(?P<score>\S+)\s*\Z",
    re.DOTALL,
)

_SCORE_DESCRIPTIONS = """SCORE DESCRIPTIONS:
1: Incorrect, contradicted, or substantially unsupported.
2: Major correctness, relevance, or grounding problems.
3: Partially correct and grounded, with meaningful weaknesses.
4: Mostly correct and grounded, with only minor omissions or imprecision.
5: Correct, relevant, sufficiently complete, and fully grounded."""


@dataclass(frozen=True)
class PrometheusGrade:
    score: int
    normalized_score: float
    feedback: str


def parse_prometheus_grade(output: str) -> PrometheusGrade:
    """Parse exactly one terminal integer result while preserving feedback."""

    if len(_RESULT_MARKER.findall(output)) != 1:
        raise PrometheusResponseError(
            "Prometheus response must contain exactly one [RESULT] score"
        )
    match = _COMPLETE_RESULT.fullmatch(output)
    if match is None:
        raise PrometheusResponseError(
            "Prometheus response must end with exactly one [RESULT] score"
        )
    raw_score = match.group("score")
    if re.fullmatch(r"[0-9]+", raw_score) is None:
        raise PrometheusResponseError("Prometheus result must be an integer")
    score = int(raw_score)
    if score < 1 or score > 5:
        raise PrometheusResponseError("Prometheus result must be between 1 and 5")
    feedback = match.group("feedback").strip()
    if not feedback:
        raise PrometheusResponseError("Prometheus response must include feedback")
    return PrometheusGrade(score, (score - 1) / 4, feedback)


class PrometheusHttpJudge:
    def __init__(self, endpoint: str, model: str = "prometheus-eval/prometheus-7b-v2.0", *, timeout_seconds: float = 30.0,
                 max_output_tokens: int = 512, retries: int = 2, client: httpx.AsyncClient | None = None) -> None:
        self.endpoint, self.model, self.timeout_seconds = endpoint.rstrip("/"), model, timeout_seconds
        self.max_output_tokens, self.retries, self.client = max_output_tokens, retries, client

    async def absolute_grade(self, instruction: str, response: str, rubric: str, reference_answer: str | None = None) -> PrometheusGrade:
        prompt = (
            f"INSTRUCTION:\n{instruction}\n\n"
            f"CANDIDATE RESPONSE:\n{response}\n\n"
            f"REFERENCE ANSWER:\n{reference_answer or '[NO REFERENCE ANSWER]'}\n\n"
            f"SCORE RUBRIC:\n{rubric}\n\n{_SCORE_DESCRIPTIONS}\n\n"
            "Return exactly: Feedback: <concise feedback> [RESULT] N, "
            "where N is one integer from 1 through 5."
        )
        text = await self._complete(prompt)
        return parse_prometheus_grade(text)

    async def _complete(self, prompt: str) -> str:
        payload = {"model": self.model, "messages": [{"role": "user", "content": prompt}], "temperature": 0,
                   "max_tokens": self.max_output_tokens}
        owns_client = self.client is None
        client = self.client or httpx.AsyncClient(timeout=self.timeout_seconds)
        try:
            for attempt in range(self.retries + 1):
                try:
                    response = await client.post(f"{self.endpoint}/v1/chat/completions", json=payload)
                    if response.status_code in {408, 429, 500, 502, 503, 504}:
                        raise PrometheusTransientError(f"Prometheus temporary HTTP {response.status_code}")
                    response.raise_for_status()
                    data = response.json()
                    text = data["choices"][0]["message"]["content"]
                    if not isinstance(text, str) or not text.strip(): raise PrometheusResponseError("Prometheus returned empty output")
                    return text
                except (httpx.TimeoutException, httpx.TransportError, PrometheusTransientError) as exc:
                    if attempt == self.retries: raise PrometheusTransientError("Prometheus transport failed") from exc
                    await asyncio.sleep(0.1 * (2 ** attempt))
        finally:
            if owns_client: await client.aclose()
        raise AssertionError("unreachable")
