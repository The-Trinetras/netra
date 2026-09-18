"""Versioned evaluation datasets, immutable snapshots and frozen held-out splits.

AX plan, step 1 ("Build a small reference dataset") and "Reproducible
datasets, experiments and recovery". A dataset file is reviewed data; a
snapshot is that data plus a content hash computed from a canonical
serialization, so two runs can prove they judged identical cases.

Rules enforced here rather than left to convention:

- Every case declares a permission (synthetic or public-licensed). Private
  student records never enter hosted-evaluation fixtures.
- Every reference answer records its author, reviewer and label status.
  Only HUMAN_GOLD with a reviewer distinct from the author counts as gold;
  SUGGESTED (for example from Alyx or a draft by an engineer/agent) is
  never gold, whatever its quality.
- A missing reference is representable. Reference-based criteria for that
  case become MISSING/reference_pending, never a judgement against an
  invented answer.
- Held-out cases are frozen into evaluation/locked/ with their own hash.
  Freezing refuses unless every held-out case has a gold reference, and a
  later load refuses a held-out set whose content changed.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from eval_results import canonical_json, sha256_hex

Split = Literal["development", "calibration", "heldout"]


class LabelStatus(str, Enum):
    HUMAN_GOLD = "human_gold"
    SUGGESTED = "suggested"
    UNREVIEWED = "unreviewed"


class ReferenceLabel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = Field(min_length=1)
    status: LabelStatus
    author: str = Field(min_length=1)
    reviewer: Optional[str] = None
    source_checked: bool = False
    """The reviewer compared the reference against the original source."""

    @model_validator(mode="after")
    def _gold_needs_independent_review(self) -> "ReferenceLabel":
        if self.status is LabelStatus.HUMAN_GOLD:
            if not self.reviewer or self.reviewer == self.author:
                raise ValueError("a human_gold reference needs a reviewer other than its author")
            if not self.source_checked:
                raise ValueError("a human_gold reference must be source-checked")
        return self

    @property
    def is_gold(self) -> bool:
        return self.status is LabelStatus.HUMAN_GOLD


class SourceExcerpt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_id: str
    source_version_id: str
    locator: str
    text: str


class JudgeCase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]*$")
    split: Split
    kind: str = Field(min_length=1)
    """The Tutor behaviour exercised, e.g. explain / evaluate_answer / hint."""
    failure_modes: list[str] = Field(default_factory=list)
    """Error-analysis categories this case covers (step 2)."""
    permission: Literal["synthetic", "public_licensed"]
    instruction: str = Field(min_length=1)
    """What the judge is told the task was: student request plus context."""
    source_excerpts: list[SourceExcerpt] = Field(default_factory=list)
    reference: Optional[ReferenceLabel] = None
    criteria: list[str] = Field(min_length=1)
    """Evaluation ids that apply to this case. Applicability is data."""
    candidate_fixture: Optional[str] = None
    """Only for fixture-replay runs: a frozen candidate response authored
    to exercise the scorer. It is labelled test data, never Netra output."""
    notes: Optional[str] = None


class DatasetFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_name: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    description: str
    cases: list[JudgeCase] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_ids(self) -> "DatasetFile":
        ids = [case.case_id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("case_id values must be unique")
        return self


class DatasetSnapshot(BaseModel):
    """An immutable, hash-identified view of a dataset file."""

    model_config = ConfigDict(frozen=True)

    dataset_name: str
    content_hash: str
    cases: tuple[JudgeCase, ...]

    @property
    def snapshot_id(self) -> str:
        return f"{self.dataset_name}@{self.content_hash[:16]}"

    def by_split(self, split: Split) -> tuple[JudgeCase, ...]:
        return tuple(case for case in self.cases if case.split == split)

    def case(self, case_id: str) -> JudgeCase:
        for case in self.cases:
            if case.case_id == case_id:
                return case
        raise KeyError(case_id)


def cases_hash(cases: tuple[JudgeCase, ...] | list[JudgeCase]) -> str:
    ordered = sorted(cases, key=lambda case: case.case_id)
    return sha256_hex(canonical_json([case.model_dump(mode="json") for case in ordered]))


def load_dataset(path: Path) -> DatasetSnapshot:
    data = DatasetFile.model_validate(json.loads(path.read_text(encoding="utf-8")))
    ordered = tuple(sorted(data.cases, key=lambda case: case.case_id))
    return DatasetSnapshot(
        dataset_name=data.dataset_name, content_hash=cases_hash(ordered), cases=ordered
    )


class FreezeRefusedError(Exception):
    pass


class HeldoutChangedError(Exception):
    pass


class FrozenHeldout(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    dataset_name: str
    heldout_hash: str
    case_ids: list[str]
    frozen_at: datetime
    frozen_by: str


def freeze_heldout(snapshot: DatasetSnapshot, frozen_by: str, locked_dir: Path) -> FrozenHeldout:
    """Freeze the held-out split. Refuses unless every held-out case is gold.

    Writes evaluation/locked/<dataset>.heldout.json. Never overwrites an
    existing freeze: a changed held-out set is a new dataset name/version,
    not a silent re-freeze.
    """

    heldout = snapshot.by_split("heldout")
    if not heldout:
        raise FreezeRefusedError("the dataset has no held-out cases")
    not_gold = [case.case_id for case in heldout if case.reference is None or not case.reference.is_gold]
    if not_gold:
        raise FreezeRefusedError(
            "held-out cases need independently reviewed, source-checked gold references: "
            + ", ".join(not_gold)
        )
    target = locked_dir / f"{snapshot.dataset_name}.heldout.json"
    if target.exists():
        raise FreezeRefusedError(f"{target.name} already exists; freezes are never overwritten")
    frozen = FrozenHeldout(
        dataset_name=snapshot.dataset_name,
        heldout_hash=cases_hash(heldout),
        case_ids=[case.case_id for case in heldout],
        frozen_at=datetime.now(timezone.utc),
        frozen_by=frozen_by,
    )
    locked_dir.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(frozen.model_dump(mode="json"), indent=2) + "\n")
    return frozen


def verify_frozen_heldout(snapshot: DatasetSnapshot, locked_dir: Path) -> FrozenHeldout:
    """Return the freeze record, or raise if the held-out set changed or was never frozen."""

    target = locked_dir / f"{snapshot.dataset_name}.heldout.json"
    if not target.exists():
        raise HeldoutChangedError(f"no frozen held-out record for {snapshot.dataset_name}")
    frozen = FrozenHeldout.model_validate(json.loads(target.read_text(encoding="utf-8")))
    heldout = snapshot.by_split("heldout")
    if [case.case_id for case in heldout] != sorted(frozen.case_ids) or cases_hash(heldout) != frozen.heldout_hash:
        raise HeldoutChangedError(
            f"held-out cases of {snapshot.dataset_name} differ from the frozen record"
        )
    return frozen
