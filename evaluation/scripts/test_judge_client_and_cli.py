"""Judge HTTP boundary, deployment gate and CLI (no network, no Modal, no AX)."""

import importlib.util
import json
from pathlib import Path

import pytest

import eval_cli
from eval_fixtures import DATASET, FIXTURE_JUDGE, producer
from eval_results import ErrorCode
from judge_client import (
    HttpxJudgeTransport,
    JudgeCallError,
    JudgeRequest,
    RunAllowance,
    error_for_status,
    reply_from_json,
)

REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("status", "body", "code", "stop", "uncertain"),
    [
        (401, None, ErrorCode.AUTH_FAILED, True, False),
        (403, None, ErrorCode.AUTH_FAILED, True, False),
        (402, None, ErrorCode.CREDIT_EXHAUSTED, True, False),
        (429, None, ErrorCode.RATE_LIMITED, True, False),
        (413, {"error": "oversized_input"}, ErrorCode.OVERSIZED_INPUT, False, False),
        (503, {"error": "out_of_memory"}, ErrorCode.OUT_OF_MEMORY, False, False),
        (409, {"error": "in_progress"}, ErrorCode.TIMEOUT_UNCERTAIN, False, True),
        (504, None, ErrorCode.TIMEOUT_UNCERTAIN, False, True),
        (500, {"error": "Traceback: secret detail"}, ErrorCode.SERVER_ERROR, False, False),
        (418, None, ErrorCode.PROTOCOL_ERROR, False, False),
    ],
)
def test_http_failures_map_to_safe_codes(status, body, code, stop, uncertain):
    error = error_for_status(status, body)
    assert (error.code, error.stop_run, error.uncertain) == (code, stop, uncertain)
    assert "secret" not in str(error)


def test_a_reply_for_another_request_is_a_protocol_error():
    with pytest.raises(JudgeCallError):
        reply_from_json({"request_id": "other", "output": "x", "finish_reason": "stop"}, "mine")
    with pytest.raises(JudgeCallError):
        reply_from_json({"request_id": "mine"}, "mine")


def test_the_transport_refuses_insecure_or_credential_bearing_urls_and_hides_secrets():
    with pytest.raises(ValueError):
        HttpxJudgeTransport("http://judge.example", "id", "secret", client=object())
    with pytest.raises(ValueError):
        HttpxJudgeTransport("https://judge.example/?token=x", "id", "secret", client=object())
    transport = HttpxJudgeTransport("https://judge.example", "TOKEN-ID", "TOKEN-SECRET", client=object())
    assert "TOKEN" not in repr(transport)


async def test_httpx_transport_sends_proxy_auth_headers_and_maps_statuses():
    httpx = pytest.importorskip("httpx")
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.path == "/score":
            body = json.loads(request.content)
            return httpx.Response(200, json={"request_id": body["request_id"], "output": "Feedback: ok [RESULT] 3",
                                             "finish_reason": "stop", "gpu_seconds": 2.5})
        if request.url.path == "/result/known":
            return httpx.Response(409, json={"error": "in_progress"})
        return httpx.Response(404, json={"error": "unknown"})

    client = httpx.AsyncClient(base_url="https://judge.example", transport=httpx.MockTransport(handler),
                               headers={"Modal-Key": "id", "Modal-Secret": "secret"})
    transport = HttpxJudgeTransport("https://judge.example", "id", "secret", client=client)
    reply = await transport.score(JudgeRequest("r-1", "prompt", 512, 4096, 0.0, 0))
    assert reply.gpu_seconds == 2.5
    assert seen[0].headers["Modal-Key"] == "id" and "secret" not in str(seen[0].url)
    assert await transport.lookup("never-seen") is None
    from judge_client import StillRunningError
    with pytest.raises(StillRunningError):
        await transport.lookup("known")
    await transport.aclose()


def test_the_allowance_stops_before_the_cap_with_a_margin():
    allowance = RunAllowance(max_gpu_seconds=100, margin_seconds=20, per_call_estimate_seconds=10)
    allowance.record(60.0, 99.0)  # server-reported GPU seconds win over wall clock
    assert allowance.spent_seconds == 60.0 and allowance.can_dispatch()
    allowance.record(None, 15.0)
    assert not allowance.can_dispatch()
    assert allowance.estimated_cost_usd() == round(75 * 0.000583, 4)


def test_the_deployment_source_fails_closed_until_pins_are_reviewed():
    path = REPO / "evaluation" / "deploy" / "prometheus_modal.py"
    spec = importlib.util.spec_from_file_location("prometheus_modal_under_test", path)
    module = importlib.util.module_from_spec(spec)
    with pytest.raises(RuntimeError, match="unreviewed pins"):
        spec.loader.exec_module(module)  # raises before `import modal`
    source = path.read_text(encoding="utf-8")
    for control in ('gpu="A100-40GB"', "max_containers=1", "min_containers=0", "buffer_containers=0",
                    "scaledown_window=60", "requires_proxy_auth=True", "max_inputs=1", "truncation=False"):
        assert control in source


# --- CLI ---------------------------------------------------------------------------


def test_cli_validate_reports_the_draft_dataset(capsys):
    assert eval_cli.main(["validate", str(DATASET)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["by_split"] == {"development": 11}
    assert out["reference_status"] == {"suggested": 10, "missing": 1}


def test_cli_refuses_to_freeze_a_dataset_without_gold_heldout_cases():
    from eval_dataset import FreezeRefusedError
    with pytest.raises(FreezeRefusedError):
        eval_cli.main(["freeze-heldout", str(DATASET), "--by", "tester"])


def test_cli_local_workflow_and_live_refusals(tmp_path, capsys, monkeypatch):
    (tmp_path / "producer.json").write_text(producer().model_dump_json(), encoding="utf-8")
    (tmp_path / "judge.json").write_text(FIXTURE_JUDGE.model_dump_json(), encoding="utf-8")
    artifacts = tmp_path / "artifacts"
    common = ["--artifacts", str(artifacts), "--run-id", "dev-1"]

    assert eval_cli.main(["init-run", *common, "--dataset", str(DATASET), "--split", "development",
                          "--criteria", "source_support_v1", "--producer", str(tmp_path / "producer.json"),
                          "--judge-config", str(tmp_path / "judge.json")]) == 0
    assert eval_cli.main(["replay-fixtures", *common, "--dataset", str(DATASET)]) == 0
    capsys.readouterr()
    assert eval_cli.main(["status", *common]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["frozen_outputs"] == 11 and status["pending_units"] == 11

    assert eval_cli.main(["judge", *common, "--dataset", str(DATASET), "--max-gpu-seconds", "10"]) == 2
    monkeypatch.delenv("NETRA_EVAL_JUDGE_URL", raising=False)
    assert eval_cli.main(["judge", *common, "--dataset", str(DATASET), "--max-gpu-seconds", "10", "--live"]) == 2
    assert eval_cli.main(["upload", *common, "--dataset", str(DATASET)]) == 2
