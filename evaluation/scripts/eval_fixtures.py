"""Test-only helpers for the evaluation suites. Not a runner, not a judge.

FakeJudgeTransport is a labelled double: it returns scripted Prometheus-
shaped text and never calls a model. Scores it produces are test data and
must never be reported as judgements.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from eval_dataset import DatasetSnapshot, load_dataset
from eval_results import ErrorCode
from eval_store import JudgeConfig, ProducerConfig, RunManifest, RunStore
from judge_client import JudgeCallError, JudgeReply, JudgeRequest, LookupUnsupportedError, StillRunningError
from judge_runner import load_rubrics

REPO = Path(__file__).resolve().parents[2]
DATASET = REPO / "evaluation" / "datasets" / "tutor_reference_v1.json"
RUBRICS = REPO / "evaluation" / "rubrics"
CRITERIA = ["source_support_v1", "factual_correctness_v1", "question_relevance_v1", "teaching_usefulness_v1"]

FIXTURE_JUDGE = JudgeConfig(
    model_id="prometheus-eval/prometheus-7b-v2.0",
    model_revision="66ffb1fc20beebfb60a3964a957d9011723116c5",
    tokenizer_revision="66ffb1fc20beebfb60a3964a957d9011723116c5",
    template_version="prometheus2-mistral-v1",
    host="fixture",
    gpu="none",
    dtype="bfloat16",
    image_digest="fixture",
    inference_library="fixture",
    max_total_tokens=4096,
    max_new_tokens=512,
    temperature=0.0,
    seed=0,
)


def producer(label: str = "fixture-baseline") -> ProducerConfig:
    return ProducerConfig(label=label, source="fixture_replay", commit="0" * 40)


def snapshot() -> DatasetSnapshot:
    return load_dataset(DATASET)


def rubrics(criteria=CRITERIA):
    return load_rubrics(RUBRICS, criteria)


def make_run(
    root: Path,
    run_id: str,
    snap: Optional[DatasetSnapshot] = None,
    criteria=CRITERIA,
    case_ids: Optional[list[str]] = None,
    judge: JudgeConfig = FIXTURE_JUDGE,
    producer_label: str = "fixture-baseline",
    freeze_fixture_outputs: bool = True,
    split: str = "development",
) -> RunStore:
    snap = snap or snapshot()
    ids = case_ids or [case.case_id for case in snap.by_split(split)]
    store = RunStore(root, run_id)
    store.create(RunManifest(
        run_id=run_id, dataset_name=snap.dataset_name, dataset_hash=snap.content_hash, split=split,
        case_ids=ids, repetitions=1, criteria=list(criteria),
        rubric_hashes={k: v[1] for k, v in rubrics(criteria).items()},
        producer=producer(producer_label), judge=judge, created_at=datetime.now(timezone.utc),
    ))
    if freeze_fixture_outputs:
        for case_id in ids:
            text = snap.case(case_id).candidate_fixture
            if text is not None:
                store.append_output(case_id, 0, text)
    return store


class FakeJudgeTransport:
    """Scripted stand-in for the Modal endpoint.

    ``script`` maps a request to either reply text, a JudgeCallError to
    raise, or None for the default "[RESULT] 3". ``server`` records what the
    'server' completed, so lookup can reconcile uncertain requests.
    """

    def __init__(self, script: Optional[Callable[[JudgeRequest], object]] = None, lookup_supported: bool = True):
        self.script = script or (lambda request: None)
        self.lookup_supported = lookup_supported
        self.score_calls: list[JudgeRequest] = []
        self.lookup_calls: list[str] = []
        self.server: dict[str, JudgeReply] = {}
        self.running: set[str] = set()

    async def score(self, request: JudgeRequest) -> JudgeReply:
        self.score_calls.append(request)
        action = self.script(request)
        if isinstance(action, JudgeCallError):
            raise action
        if isinstance(action, tuple):  # ("complete_then_timeout", text)
            _, text = action
            self.server[request.request_id] = JudgeReply(request.request_id, text, "stop", gpu_seconds=1.0)
            raise JudgeCallError(ErrorCode.TIMEOUT_UNCERTAIN, uncertain=True)
        text = action if isinstance(action, str) else "Feedback: scripted fixture feedback. [RESULT] 3"
        reply = JudgeReply(request.request_id, text, "stop", gpu_seconds=1.0)
        self.server[request.request_id] = reply
        return reply

    async def lookup(self, request_id: str) -> Optional[JudgeReply]:
        self.lookup_calls.append(request_id)
        if not self.lookup_supported:
            raise LookupUnsupportedError("fixture")
        if request_id in self.running:
            raise StillRunningError(request_id)
        return self.server.get(request_id)
