import httpx
import pytest

from prometheus_client import (
    PrometheusHttpJudge,
    PrometheusResponseError,
    PrometheusTransientError,
    parse_prometheus_grade,
)


def _client(status=200, content='Feedback\n[RESULT] 4'):
    async def handler(_request): return httpx.Response(status, json={"choices": [{"message": {"content": content}}]})
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_prometheus_score_is_strictly_normalized():
    grade = await PrometheusHttpJudge("http://judge", client=_client()).absolute_grade("q", "a", "r")
    assert grade.score == 4 and grade.normalized_score == .75
    assert grade.feedback == "Feedback"


@pytest.mark.parametrize(
    ("score", "normalized"),
    [(1, 0.0), (3, 0.5), (5, 1.0)],
)
def test_prometheus_accepts_each_boundary_and_midpoint_score(score, normalized):
    grade = parse_prometheus_grade(f"Feedback: grounded\n[RESULT] {score}")
    assert grade.score == score
    assert grade.normalized_score == normalized
    assert grade.feedback == "Feedback: grounded"


@pytest.mark.asyncio
async def test_prometheus_rejects_missing_or_conflicting_scores():
    with pytest.raises(PrometheusResponseError):
        await PrometheusHttpJudge("http://judge", client=_client(content="no score")).absolute_grade("q", "a", "r")
    with pytest.raises(PrometheusResponseError):
        await PrometheusHttpJudge("http://judge", client=_client(content="[RESULT] 2 [RESULT] 3")).absolute_grade("q", "a", "r")


@pytest.mark.parametrize(
    "output",
    [
        "Feedback: no marker",
        "Feedback: decimal [RESULT] 3.0",
        "Feedback: too low [RESULT] 0",
        "Feedback: too high [RESULT] 6",
        "Feedback: trailing [RESULT] 3 extra",
        "[RESULT] 3",
    ],
)
def test_prometheus_rejects_malformed_or_out_of_range_results(output):
    with pytest.raises(PrometheusResponseError):
        parse_prometheus_grade(output)


@pytest.mark.asyncio
async def test_prometheus_retries_only_transient_statuses():
    with pytest.raises(PrometheusTransientError):
        await PrometheusHttpJudge("http://judge", retries=0, client=_client(status=503)).absolute_grade("q", "a", "r")
