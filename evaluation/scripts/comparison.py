"""Paired baseline/candidate comparison on identical frozen conditions.

AX plan, "Reproducible datasets, experiments and recovery":

- Both runs must share the dataset snapshot, split, case ids, criteria,
  rubric hashes and judge configuration. Otherwise the comparison is
  refused; a changed judge or rubric means rescoring BOTH frozen output
  sets under a new comparison identity (see rescore_manifest).
- Held-out comparisons require a frozen held-out record whose hash matches.
- The report is per case and criterion: paired deltas, ordinal
  distributions per arm, regressions, and explicit denominators — expected
  pairs, complete pairs, and pairs missing on either side with reasons.
  An incomplete pair is never counted as improvement.
- Critical deterministic failures (access, evidence, state, accessibility)
  are listed first and block any "improved" reading regardless of scores.
- Trace completeness is reported per case from the reconciliation manifest
  M1/M4 produce; unknown is reported as unknown, not complete.
- Pairwise (A/B) preferences are checked in both orders; inconsistent
  preferences are flagged for human review and kept apart from absolute
  scores.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict

from eval_dataset import DatasetSnapshot, verify_frozen_heldout
from eval_results import Outcome
from eval_store import JudgeConfig, RunManifest, RunStore


class ComparisonIdentityError(Exception):
    pass


class DeterministicFinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    arm: Literal["baseline", "candidate"]
    case_id: str
    check_id: str
    passed: bool
    critical: bool
    detail: Optional[str] = None


TraceStatus = Literal["complete", "pending", "incomplete", "unknown"]


@dataclass
class PairedRow:
    case_id: str
    repetition: int
    criterion_id: str
    baseline: Optional[int]
    candidate: Optional[int]
    baseline_outcome: str
    candidate_outcome: str

    @property
    def complete(self) -> bool:
        return self.baseline is not None and self.candidate is not None

    @property
    def delta(self) -> Optional[int]:
        return self.candidate - self.baseline if self.complete else None  # type: ignore[operator]


@dataclass
class ComparisonReport:
    comparison_id: str
    baseline_run: str
    candidate_run: str
    judge_config_id: str
    rows: list[PairedRow] = field(default_factory=list)
    critical_failures: list[DeterministicFinding] = field(default_factory=list)
    other_deterministic_failures: list[DeterministicFinding] = field(default_factory=list)
    trace_status: dict[str, dict[str, TraceStatus]] = field(default_factory=dict)

    def per_criterion(self) -> dict[str, dict]:
        summary: dict[str, dict] = {}
        for row in self.rows:
            entry = summary.setdefault(row.criterion_id, {
                "expected_pairs": 0, "complete_pairs": 0, "missing_baseline": 0,
                "missing_candidate": 0, "improved": 0, "regressed": 0, "unchanged": 0,
                "baseline_distribution": {str(s): 0 for s in range(1, 6)},
                "candidate_distribution": {str(s): 0 for s in range(1, 6)},
                "not_applicable": 0,
            })
            if row.baseline_outcome == row.candidate_outcome == Outcome.NOT_APPLICABLE.value:
                entry["not_applicable"] += 1
                continue
            entry["expected_pairs"] += 1
            if row.baseline is not None:
                entry["baseline_distribution"][str(row.baseline)] += 1
            else:
                entry["missing_baseline"] += 1
            if row.candidate is not None:
                entry["candidate_distribution"][str(row.candidate)] += 1
            else:
                entry["missing_candidate"] += 1
            if row.complete:
                entry["complete_pairs"] += 1
                delta = row.delta
                entry["improved" if delta > 0 else "regressed" if delta < 0 else "unchanged"] += 1  # type: ignore[operator]
        return summary

    @property
    def regressions(self) -> list[PairedRow]:
        return [row for row in self.rows if row.complete and row.delta < 0]  # type: ignore[operator]

    @property
    def verdict(self) -> str:
        """A reading, not a decision. Humans decide acceptance."""

        if self.critical_failures:
            return "blocked_by_critical_deterministic_failures"
        per = self.per_criterion()
        if any(c["complete_pairs"] < c["expected_pairs"] for c in per.values()):
            return "partial_comparison_incomplete_pairs"
        if any(status != "complete" for arms in self.trace_status.values() for status in arms.values()):
            return "scores_complete_traces_incomplete"
        if not self.trace_status:
            return "scores_complete_trace_status_unknown"
        return "complete_pairs_for_human_review"


def comparison_identity(baseline: RunManifest, candidate: RunManifest) -> str:
    for attribute in ("dataset_hash", "split", "case_ids", "repetitions", "criteria", "rubric_hashes"):
        if getattr(baseline, attribute) != getattr(candidate, attribute):
            raise ComparisonIdentityError(f"runs differ in {attribute}; rescore both under one configuration")
    if baseline.judge != candidate.judge:
        raise ComparisonIdentityError("runs were judged under different judge configurations; rescore both")
    if baseline.run_id == candidate.run_id:
        raise ComparisonIdentityError("a run cannot be compared with itself")
    return f"cmp-{baseline.dataset_hash[:12]}-{baseline.judge.judge_config_id}-{baseline.run_id}-vs-{candidate.run_id}"


def rescore_manifest(original: RunManifest, new_run_id: str, judge: JudgeConfig, rubric_hashes: dict[str, str]) -> RunManifest:
    """Manifest for rescoring an existing run's frozen outputs under a new
    judge/rubric configuration. The outputs are copied, never regenerated."""

    return original.model_copy(update={"run_id": new_run_id, "judge": judge, "rubric_hashes": rubric_hashes})


def compare_runs(
    baseline: RunStore,
    candidate: RunStore,
    snapshot: DatasetSnapshot,
    locked_dir: Path,
    deterministic: list[DeterministicFinding] = (),  # type: ignore[assignment]
    trace_status: Optional[dict[str, dict[str, TraceStatus]]] = None,
) -> ComparisonReport:
    base_manifest, cand_manifest = baseline.manifest(), candidate.manifest()
    comparison_id = comparison_identity(base_manifest, cand_manifest)
    if base_manifest.dataset_hash != snapshot.content_hash:
        raise ComparisonIdentityError("the snapshot does not match the runs' dataset")
    if base_manifest.split == "heldout":
        verify_frozen_heldout(snapshot, locked_dir)

    def by_unit(store: RunStore):
        return {(k.case_id, k.repetition, k.criterion_id): r for k, r in store.effective_results().items()}

    base_results, cand_results = by_unit(baseline), by_unit(candidate)
    report = ComparisonReport(
        comparison_id=comparison_id,
        baseline_run=base_manifest.run_id,
        candidate_run=cand_manifest.run_id,
        judge_config_id=base_manifest.judge.judge_config_id,
    )
    for case_id in base_manifest.case_ids:
        for repetition in range(base_manifest.repetitions):
            for criterion_id in base_manifest.criteria:
                unit = (case_id, repetition, criterion_id)
                b, c = base_results.get(unit), cand_results.get(unit)
                report.rows.append(PairedRow(
                    case_id=case_id, repetition=repetition, criterion_id=criterion_id,
                    baseline=b.score if b is not None and b.outcome is Outcome.SCORED else None,
                    candidate=c.score if c is not None and c.outcome is Outcome.SCORED else None,
                    baseline_outcome=_describe(b), candidate_outcome=_describe(c),
                ))
    for finding in deterministic:
        if not finding.passed:
            (report.critical_failures if finding.critical else report.other_deterministic_failures).append(finding)
    report.trace_status = {
        case_id: (trace_status or {}).get(case_id, {"baseline": "unknown", "candidate": "unknown"})
        for case_id in base_manifest.case_ids
    } if trace_status is not None else {}
    return report


def _describe(result) -> str:
    if result is None:
        return "pending"
    if result.error_code is not None:
        return f"{result.outcome.value}:{result.error_code.value}"
    return result.outcome.value


@dataclass(frozen=True)
class PairwiseJudgement:
    case_id: str
    criterion_id: str
    order: Literal["baseline_first", "candidate_first"]
    preferred: Optional[Literal["A", "B"]]
    """None when the judge output was invalid."""


def pairwise_consistency(judgements: list[PairwiseJudgement]) -> dict[str, list[str]]:
    """Map each (case, criterion) to its resolved preference, or flag it.

    In baseline_first order A is the baseline; in candidate_first order A is
    the candidate. A consistent pair names the same underlying response in
    both orders. Anything else goes to human review.
    """

    grouped: dict[tuple[str, str], dict[str, Optional[str]]] = {}
    for j in judgements:
        grouped.setdefault((j.case_id, j.criterion_id), {})[j.order] = j.preferred
    outcome: dict[str, list[str]] = {"prefers_baseline": [], "prefers_candidate": [], "needs_human_review": []}
    for (case_id, criterion_id), orders in grouped.items():
        label = f"{case_id}/{criterion_id}"
        first, second = orders.get("baseline_first"), orders.get("candidate_first")
        if first is None or second is None:
            outcome["needs_human_review"].append(label)
            continue
        first_pick = "baseline" if first == "A" else "candidate"
        second_pick = "candidate" if second == "A" else "baseline"
        if first_pick != second_pick:
            outcome["needs_human_review"].append(label)
        else:
            outcome[f"prefers_{first_pick}"].append(label)
    return outcome


def render_markdown(report: ComparisonReport) -> str:
    lines = [
        f"# Paired comparison {report.comparison_id}",
        "",
        f"Baseline `{report.baseline_run}` vs candidate `{report.candidate_run}`, judge `{report.judge_config_id}`.",
        f"Reading: **{report.verdict}** (humans decide acceptance; small samples establish no significance).",
        "",
    ]
    if report.critical_failures:
        lines += ["## Critical deterministic failures", ""]
        lines += [f"- {f.arm} `{f.case_id}` {f.check_id}: {f.detail or 'failed'}" for f in report.critical_failures]
        lines.append("")
    lines += ["## Per criterion", "", "| criterion | expected | complete | missing base | missing cand | improved | regressed | unchanged | n/a |", "|---|---|---|---|---|---|---|---|---|"]
    for criterion_id, c in sorted(report.per_criterion().items()):
        lines.append(
            f"| {criterion_id} | {c['expected_pairs']} | {c['complete_pairs']} | {c['missing_baseline']} | "
            f"{c['missing_candidate']} | {c['improved']} | {c['regressed']} | {c['unchanged']} | {c['not_applicable']} |"
        )
    lines += ["", "## Regressions", ""]
    lines += [f"- `{r.case_id}` r{r.repetition} {r.criterion_id}: {r.baseline} -> {r.candidate}" for r in report.regressions] or ["- none among complete pairs"]
    lines += ["", "## Incomplete pairs", ""]
    incomplete = [r for r in report.rows if not r.complete and not (r.baseline_outcome == r.candidate_outcome == "not_applicable")]
    lines += [f"- `{r.case_id}` r{r.repetition} {r.criterion_id}: baseline {r.baseline_outcome}, candidate {r.candidate_outcome}" for r in incomplete] or ["- none"]
    lines += ["", "## Trace completeness", ""]
    if report.trace_status:
        lines += [f"- `{case}`: baseline {s['baseline']}, candidate {s['candidate']}" for case, s in sorted(report.trace_status.items())]
    else:
        lines.append("- not supplied (no reconciled trace manifest)")
    return "\n".join(lines) + "\n"
