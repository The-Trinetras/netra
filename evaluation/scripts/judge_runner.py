"""Resumable Prometheus judging over frozen candidate outputs.

AX plan, step 5, and the recovery rules in model-evaluation-plan.md:

- Only frozen outputs are judged. The runner never produces candidate
  output and never re-runs Netra.
- One unit at a time (one in-flight request), each result persisted before
  the next dispatch. Resume skips units that already have a terminal
  result, so rerunning a finished run makes no judge call.
- A timeout or transport failure after sending is UNCERTAIN: the unit is
  persisted as FAILED/timeout_uncertain with its request_id, and the next
  resume reconciles it through the endpoint's lookup before any retry. If
  reconciliation is impossible the run stops with that unit unresolved —
  it is never blindly re-sent.
- Authentication failure, credit exhaustion and rate limiting stop the
  run. Units never dispatched stay pending; units skipped because the
  allowance ran out are persisted MISSING/budget_exhausted. Neither is a
  pass or a zero.
- Criterion applicability and reference availability come from the
  dataset. A missing reference produces MISSING/reference_pending, never a
  judgement against an invented answer.
- Cache reuse across runs is opt-in, keyed by the full input hash, and
  labelled with ``reused_from``.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Optional

from eval_dataset import DatasetSnapshot, JudgeCase
from eval_results import (
    CriterionResult,
    ErrorCode,
    Outcome,
    UnitKey,
    canonical_json,
    judge_input_hash,
    sha256_hex,
)
from eval_store import RunStore, is_terminal
from judge_client import (
    JudgeCallError,
    JudgeReply,
    JudgeRequest,
    JudgeTransport,
    LookupUnsupportedError,
    RunAllowance,
    StillRunningError,
)
from prometheus import TEMPLATE_VERSION, AnchoredRubric, build_absolute_prompt, parse_absolute


def load_rubrics(rubric_dir: Path, evaluation_ids: Iterable[str]) -> dict[str, tuple[AnchoredRubric, str]]:
    """Load ``<evaluation_id>.json`` rubrics with a hash of their content.

    The hash is over canonical JSON, not file bytes, so line endings and
    formatting (which differ between checkouts) never change run identity,
    while any change to a field does.
    """

    loaded: dict[str, tuple[AnchoredRubric, str]] = {}
    for evaluation_id in evaluation_ids:
        data = json.loads((rubric_dir / f"{evaluation_id}.json").read_text(encoding="utf-8"))
        rubric = AnchoredRubric.model_validate(data)
        if rubric.evaluation_id != evaluation_id:
            raise ValueError(f"{evaluation_id}.json declares {rubric.evaluation_id}")
        loaded[evaluation_id] = (rubric, sha256_hex(canonical_json(data)))
    return loaded


class RunIdentityError(Exception):
    """The store, dataset and rubrics do not describe the same run."""


class ReconciliationRequiredError(Exception):
    """An uncertain request cannot be reconciled; operator action needed."""

    def __init__(self, key: UnitKey, request_id: Optional[str]) -> None:
        self.key = key
        self.request_id = request_id
        super().__init__(f"{key.as_str()} has an unreconciled judge request {request_id}")


@dataclass
class RunSummary:
    judged: int = 0
    reconciled: int = 0
    reused: int = 0
    skipped_terminal: int = 0
    not_applicable: int = 0
    reference_pending: int = 0
    invalid: int = 0
    failed: int = 0
    budget_exhausted: int = 0
    missing_outputs: list[str] = field(default_factory=list)
    still_running: list[str] = field(default_factory=list)
    stopped_reason: Optional[str] = None


def instruction_for(case: JudgeCase) -> str:
    """The task the judge sees: the case instruction plus its source excerpts.

    Excerpts are untrusted data inserted verbatim; source support cannot be
    judged without them.
    """

    if not case.source_excerpts:
        return case.instruction
    excerpts = "\n".join(
        f"[{item.evidence_id}] ({item.locator}) {item.text}" for item in case.source_excerpts
    )
    return f"{case.instruction}\n\nSource excerpts available to the tutor:\n{excerpts}"


def request_id_for(key: UnitKey, attempt: int) -> str:
    return f"{key.as_str()}#a{attempt}"


def _result(key: UnitKey, outcome: Outcome, config_id: str, input_hash: str, **extra) -> CriterionResult:
    return CriterionResult(key=key, outcome=outcome, judge_config_id=config_id, input_hash=input_hash, **extra)


def _from_reply(key, reply: JudgeReply, config_id, input_hash, elapsed_ms) -> CriterionResult:
    parsed = parse_absolute(reply.output, reply.finish_reason)
    if parsed.error is not None:
        return _result(key, Outcome.INVALID, config_id, input_hash, error_code=parsed.error,
                       request_id=reply.request_id, elapsed_ms=elapsed_ms)
    assert parsed.result is not None
    return _result(key, Outcome.SCORED, config_id, input_hash, score=int(parsed.result),
                   feedback=parsed.feedback, request_id=reply.request_id, elapsed_ms=elapsed_ms)


def _check_identity(store: RunStore, snapshot: DatasetSnapshot, rubrics: Mapping[str, tuple[AnchoredRubric, str]]) -> None:
    manifest = store.manifest()
    if manifest.dataset_hash != snapshot.content_hash:
        raise RunIdentityError("the dataset changed since this run was created")
    for evaluation_id in manifest.criteria:
        if evaluation_id not in rubrics or rubrics[evaluation_id][1] != manifest.rubric_hashes.get(evaluation_id):
            raise RunIdentityError(f"rubric {evaluation_id} differs from the one this run was created with")


async def judge_run(
    store: RunStore,
    snapshot: DatasetSnapshot,
    rubrics: Mapping[str, tuple[AnchoredRubric, str]],
    transport: JudgeTransport,
    allowance: RunAllowance,
    cache: Optional[Mapping[str, tuple[str, CriterionResult]]] = None,
) -> RunSummary:
    """Judge every pending unit of the run in ``store``. Safe to call again.

    ``cache`` maps input_hash -> (source run_id, result) from other runs
    and is used only when supplied explicitly.
    """

    _check_identity(store, snapshot, rubrics)
    manifest = store.manifest()
    config = manifest.judge
    config_id = config.judge_config_id
    outputs = store.outputs()
    summary = RunSummary()
    history = store.result_history()

    for case_id in manifest.case_ids:
        case: JudgeCase = snapshot.case(case_id)
        for repetition in range(manifest.repetitions):
            output = outputs.get((case_id, repetition))
            for criterion_id in manifest.criteria:
                key = UnitKey(run_id=manifest.run_id, case_id=case_id, repetition=repetition, criterion_id=criterion_id)
                records = history.get(key, [])
                latest = records[-1].result if records else None
                if latest is not None and is_terminal(latest):
                    summary.skipped_terminal += 1
                    continue

                rubric, rubric_hash = rubrics[criterion_id]
                if criterion_id not in case.criteria:
                    store.append_result(_result(key, Outcome.NOT_APPLICABLE, config_id, "n/a"))
                    summary.not_applicable += 1
                    continue
                if output is None:
                    summary.missing_outputs.append(key.as_str())
                    continue
                if rubric.requires_reference and case.reference is None:
                    store.append_result(_result(key, Outcome.MISSING, config_id, "n/a",
                                                error_code=ErrorCode.REFERENCE_PENDING))
                    summary.reference_pending += 1
                    continue

                reference = case.reference.text if case.reference is not None else "Not provided."
                instruction = instruction_for(case)
                prompt = build_absolute_prompt(rubric, instruction, output.response, reference)
                input_hash = judge_input_hash(
                    template=TEMPLATE_VERSION, judge=config_id, rubric=rubric_hash,
                    instruction=instruction, response_hash=output.response_hash, reference=reference,
                )

                # Reconcile an uncertain earlier attempt before anything else.
                if latest is not None and latest.error_code is ErrorCode.TIMEOUT_UNCERTAIN:
                    try:
                        reply = await transport.lookup(latest.request_id or "")
                    except StillRunningError:
                        summary.still_running.append(key.as_str())
                        continue
                    except LookupUnsupportedError as unsupported:
                        raise ReconciliationRequiredError(key, latest.request_id) from unsupported
                    if reply is not None:
                        store.append_result(_from_reply(key, reply, config_id, input_hash, latest.elapsed_ms))
                        summary.reconciled += 1
                        continue
                    # The server never saw it: a new attempt is safe.

                if cache is not None and input_hash in cache:
                    source_run, cached = cache[input_hash]
                    if cached.outcome is Outcome.SCORED:
                        store.append_result(cached.model_copy(update={"key": key, "reused_from": source_run}))
                        summary.reused += 1
                        continue

                if not allowance.can_dispatch():
                    store.append_result(_result(key, Outcome.MISSING, config_id, input_hash,
                                                error_code=ErrorCode.BUDGET_EXHAUSTED))
                    summary.budget_exhausted += 1
                    summary.stopped_reason = summary.stopped_reason or ErrorCode.BUDGET_EXHAUSTED.value
                    continue

                request = JudgeRequest(
                    request_id=request_id_for(key, len(records) + 1),
                    prompt=prompt,
                    max_new_tokens=config.max_new_tokens,
                    max_total_tokens=config.max_total_tokens,
                    temperature=config.temperature,
                    seed=config.seed,
                )
                started = time.monotonic()
                try:
                    reply = await transport.score(request)
                except JudgeCallError as failure:
                    elapsed = time.monotonic() - started
                    allowance.record(None, elapsed)
                    store.append_result(_result(key, Outcome.FAILED, config_id, input_hash,
                                                error_code=failure.code, request_id=request.request_id,
                                                elapsed_ms=int(elapsed * 1000)))
                    summary.failed += 1
                    if failure.stop_run or failure.uncertain:
                        # Uncertain: stop so the next run reconciles first
                        # instead of piling more work behind an unknown one.
                        summary.stopped_reason = failure.code.value
                        return summary
                    continue
                elapsed = time.monotonic() - started
                allowance.record(reply.gpu_seconds, elapsed, reply.cold_start_seconds)
                result = _from_reply(key, reply, config_id, input_hash, int(elapsed * 1000))
                store.append_result(result)
                summary.judged += 1
                if result.outcome is Outcome.INVALID:
                    summary.invalid += 1
    return summary
