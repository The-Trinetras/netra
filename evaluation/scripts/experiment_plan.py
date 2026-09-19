"""A before/after experiment plan fixed BEFORE any candidate result is seen.

AX plan: compare baseline and candidate on the same held-out snapshot,
references, criterion applicability, rubrics and judge configuration, and
record intended and confounding producer changes. A plan pins all of that,
plus the case pairing, the repetitions, each arm's producer configuration and
the acceptance criteria humans predeclare. check_run() verifies that a run
store matches its arm of the plan, so a mismatched run cannot be compared by
accident.

Acceptance criteria are human-written text. This module never invents a
passing threshold; a plan with none says so explicitly.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from eval_dataset import DatasetSnapshot
from eval_results import canonical_json, sha256_hex
from eval_store import JudgeConfig, ProducerConfig, RunManifest

Arm = Literal["baseline", "candidate"]


class ArmPlan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    producer: ProducerConfig


class ExperimentPlan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    plan_version: Literal["netra-experiment-plan-v1"] = "netra-experiment-plan-v1"
    dataset_name: str
    dataset_hash: str
    split: Literal["development", "calibration", "heldout"]
    case_ids: list[str] = Field(min_length=1)
    repetitions: int = Field(ge=1)
    criteria: list[str] = Field(min_length=1)
    rubric_hashes: dict[str, str]
    judge: JudgeConfig
    baseline: ArmPlan
    candidate: ArmPlan
    acceptance_criteria: list[str] = Field(default_factory=list)
    """Human-written before results are inspected. Empty means 'none declared'."""
    heldout_freeze_required: bool = True
    created_at: datetime
    notes: Optional[str] = None

    @property
    def plan_id(self) -> str:
        body = self.model_dump(mode="json", exclude={"created_at"})
        return "plan-" + sha256_hex(canonical_json(body))[:16]


def make_plan(snapshot: DatasetSnapshot, split: str, criteria: list[str], rubric_hashes: dict[str, str],
              judge: JudgeConfig, baseline: ArmPlan, candidate: ArmPlan, repetitions: int = 1,
              acceptance_criteria: Optional[list[str]] = None, notes: Optional[str] = None) -> ExperimentPlan:
    if baseline.run_id == candidate.run_id:
        raise ValueError("baseline and candidate need different run ids")
    if baseline.producer.source != candidate.producer.source:
        raise ValueError("both arms must use the same producer kind (netra vs fixture_replay)")
    cases = [case.case_id for case in snapshot.by_split(split)]  # type: ignore[arg-type]
    if not cases:
        raise ValueError(f"no {split} cases in {snapshot.snapshot_id}")
    return ExperimentPlan(
        dataset_name=snapshot.dataset_name, dataset_hash=snapshot.content_hash, split=split,  # type: ignore[arg-type]
        case_ids=cases, repetitions=repetitions, criteria=criteria, rubric_hashes=rubric_hashes, judge=judge,
        baseline=baseline, candidate=candidate, acceptance_criteria=acceptance_criteria or [],
        created_at=datetime.now(timezone.utc), notes=notes,
    )


def manifest_for(plan: ExperimentPlan, arm: Arm) -> RunManifest:
    chosen = plan.baseline if arm == "baseline" else plan.candidate
    return RunManifest(run_id=chosen.run_id, dataset_name=plan.dataset_name, dataset_hash=plan.dataset_hash,
                       split=plan.split, case_ids=plan.case_ids, repetitions=plan.repetitions, criteria=plan.criteria,
                       rubric_hashes=plan.rubric_hashes, producer=chosen.producer, judge=plan.judge,
                       created_at=datetime.now(timezone.utc))


def check_run(plan: ExperimentPlan, arm: Arm, manifest: RunManifest) -> list[str]:
    """Every way the run differs from its arm of the plan (empty = matches)."""

    expected = manifest_for(plan, arm)
    problems = []
    for field in ("run_id", "dataset_name", "dataset_hash", "split", "case_ids", "repetitions", "criteria",
                  "rubric_hashes", "producer", "judge"):
        if getattr(manifest, field) != getattr(expected, field):
            problems.append(f"{field} differs from the plan")
    return problems


def write_plan(plan: ExperimentPlan, path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return plan.plan_id


def load_plan(path: Path) -> ExperimentPlan:
    return ExperimentPlan.model_validate(json.loads(path.read_text(encoding="utf-8")))
