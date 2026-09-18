"""Judge calibration against human labels (AX plan step 4; judge plan step 5).

Compares Prometheus SCORED results with human-labelled development/
calibration cases, per criterion: exact agreement, within-one agreement,
mean absolute error, the full confusion table, every disagreement, and
false-high cases (judge >= 4 where the human gave <= 2 — the unsupported
answer scored well). Non-scored judge outcomes are counted, never
imputed. No passing threshold is invented: humans adjudicate.

Held-out runs are refused. Frozen held-out cases must not be used to tune
rubrics or prompts, and calibrating on them would make them seen.
Ten labelled cases per criterion is the plan's starting floor, not proof
of reliability; below it the report says so.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from eval_results import Outcome
from eval_store import RunStore

CALIBRATION_FLOOR = 10


class HumanLabel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str
    repetition: int = Field(default=0, ge=0)
    criterion_id: str
    score: int = Field(ge=1, le=5)
    labeller: str = Field(min_length=1)
    adjudicated_score: Optional[int] = Field(default=None, ge=1, le=5)
    """Set when a second human resolved a disagreement; used instead of score."""
    adjudicator: Optional[str] = None

    @property
    def final(self) -> int:
        return self.adjudicated_score if self.adjudicated_score is not None else self.score


class CalibrationRefusedError(Exception):
    pass


@dataclass
class CriterionCalibration:
    criterion_id: str
    labelled: int = 0
    compared: int = 0
    judge_not_scored: dict[str, int] = field(default_factory=dict)
    exact: int = 0
    within_one: int = 0
    abs_error_total: int = 0
    confusion: dict[str, int] = field(default_factory=dict)
    """"human->judge" -> count."""
    disagreements: list[dict] = field(default_factory=list)
    false_high: list[str] = field(default_factory=list)

    @property
    def exact_rate(self) -> Optional[float]:
        return self.exact / self.compared if self.compared else None

    @property
    def within_one_rate(self) -> Optional[float]:
        return self.within_one / self.compared if self.compared else None

    @property
    def mean_abs_error(self) -> Optional[float]:
        return self.abs_error_total / self.compared if self.compared else None

    @property
    def below_floor(self) -> bool:
        return self.compared < CALIBRATION_FLOOR


def calibrate(store: RunStore, labels: list[HumanLabel]) -> dict[str, CriterionCalibration]:
    manifest = store.manifest()
    if manifest.split == "heldout":
        raise CalibrationRefusedError("frozen held-out cases must not be used for calibration")
    results = store.effective_results()
    by_unit = {(k.case_id, k.repetition, k.criterion_id): r for k, r in results.items()}

    report: dict[str, CriterionCalibration] = {}
    for label in labels:
        entry = report.setdefault(label.criterion_id, CriterionCalibration(label.criterion_id))
        entry.labelled += 1
        result = by_unit.get((label.case_id, label.repetition, label.criterion_id))
        if result is None or result.outcome is not Outcome.SCORED:
            reason = "not_judged" if result is None else (
                result.outcome.value if result.error_code is None else f"{result.outcome.value}:{result.error_code.value}"
            )
            entry.judge_not_scored[reason] = entry.judge_not_scored.get(reason, 0) + 1
            continue
        assert result.score is not None
        human, judge = label.final, result.score
        entry.compared += 1
        entry.exact += human == judge
        entry.within_one += abs(human - judge) <= 1
        entry.abs_error_total += abs(human - judge)
        cell = f"{human}->{judge}"
        entry.confusion[cell] = entry.confusion.get(cell, 0) + 1
        if human != judge:
            entry.disagreements.append(
                {"case_id": label.case_id, "repetition": label.repetition, "human": human,
                 "judge": judge, "labeller": label.labeller, "judge_feedback": result.feedback}
            )
        if judge >= 4 and human <= 2:
            entry.false_high.append(f"{label.case_id}/r{label.repetition}")
    return report
