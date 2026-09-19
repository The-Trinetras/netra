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

Source-grounded cases (netra-grounded-v1 onward) may also carry the
structured fields below: the student's input and conversation, session
context, evidence deliberately withheld from the producer, the expected
behaviour with its justification, deterministic assertions, checkable
calculations, provenance, limitations and prior exposure. All of them are
optional and are left out of the content hash while at their defaults, so
datasets written before they existed keep their original hash
(tutor-reference-v1 stays e942307c...). dataset_checks.py and grounding.py
enforce the rules these fields make possible (split leakage, exposure,
verbatim evidence, calculations).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, Literal, Optional, Union

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
    rationale: Optional[str] = None
    """Why this behaviour is the right one, in terms of the evidence."""
    acceptable_alternatives: list[str] = Field(default_factory=list)
    """Other responses that are also correct (different reasoning or wording)."""
    ambiguity: Optional[str] = None
    """Known ambiguity a reviewer should resolve; None when none is known."""

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
    source_key: Optional[str] = None
    """Registry source (evaluation/sources); grounding.py checks the text verbatim."""
    variant: Optional[str] = None
    """A named fixture variant of the same object (e.g. unreadable_axes)."""
    trust: Optional[Literal["source_verified", "derived", "source_mismatch", "unreadable"]] = None
    start_ms: Optional[int] = Field(default=None, ge=0)
    end_ms: Optional[int] = Field(default=None, ge=0)


WithheldReason = Literal[
    "denied_other_account",
    "deleted_source",
    "stale_inactive_version",
    "failed_version",
    "version_not_pinned",
    "not_found",
]


class WithheldEvidence(BaseModel):
    """Evidence that exists in the scenario but must not reach the answer.

    The producer never receives it; its text is not copied into the case.
    Assertions reference it so a response that uses or cites it fails.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_id: str
    source_version_id: str
    source_key: str
    reason: WithheldReason


class DialogueTurn(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    role: Literal["student", "netra"]
    content: str = Field(min_length=1)
    note: Optional[str] = None


class SessionContext(BaseModel):
    """Session facts the producer is given (never references or labels)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pinned_source_version_ids: list[str] = Field(default_factory=list)
    interaction_mode: Optional[Literal["idle", "reading", "tutor_lesson", "quiz"]] = None
    player_time_ms: Optional[int] = Field(default=None, ge=0)
    pending_question: Optional[str] = None
    notes: Optional[str] = None


class _Assertion(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    critical: bool = False
    """A critical failure (access, evidence, privacy, injection) blocks any
    'improved' reading of a comparison regardless of judge scores."""
    reason: Optional[str] = None


class MustCiteAny(_Assertion):
    type: Literal["must_cite_any"] = "must_cite_any"
    evidence_ids: list[str] = Field(min_length=1)


class MustNotCite(_Assertion):
    type: Literal["must_not_cite"] = "must_not_cite"
    evidence_ids: list[str] = Field(min_length=1)


class CitesOnlySupplied(_Assertion):
    type: Literal["cites_only_supplied"] = "cites_only_supplied"


class StatesQuantity(_Assertion):
    type: Literal["states_quantity"] = "states_quantity"
    value: float
    unit: str = Field(min_length=1)


class MustNotStateQuantity(_Assertion):
    type: Literal["must_not_state_quantity"] = "must_not_state_quantity"
    value: float
    unit: str = Field(min_length=1)


class MustNotContain(_Assertion):
    type: Literal["must_not_contain"] = "must_not_contain"
    phrases: list[str] = Field(min_length=1)


class PendingQuestion(_Assertion):
    """Structured-result check: whether the turn leaves a question pending."""

    type: Literal["pending_question"] = "pending_question"
    expected: bool


class NoLearningEvent(_Assertion):
    """Structured-result check: the turn proposes no learning event/grade."""

    type: Literal["no_learning_event"] = "no_learning_event"


Assertion = Annotated[
    Union[MustCiteAny, MustNotCite, CitesOnlySupplied, StatesQuantity, MustNotStateQuantity,
          MustNotContain, PendingQuestion, NoLearningEvent],
    Field(discriminator="type"),
]


class Quantity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    value: float
    unit: str = Field(min_length=1)
    evidence_id: Optional[str] = None
    """Where this input is stated; grounding.py checks it appears there."""


class Calculation(BaseModel):
    """A calculation the reference relies on, recomputed by grounding.py."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    calc_id: str = Field(min_length=1)
    operation: Literal["divide", "multiply", "add", "parallel"]
    operands: list[Quantity] = Field(min_length=2)
    expected: Quantity
    in_reference: bool = True
    """The reference states the result (False for abstention/withheld-answer cases)."""


class CaseProvenance(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    origin: Literal["project_fixture", "new_synthetic_miniature", "existing_dataset"]
    """project_fixture: grounded in source fixtures that already existed in the
    repository; new_synthetic_miniature: self-authored miniature source, clearly
    labelled; existing_dataset: carried over from an earlier dataset."""
    source_files: list[str] = Field(default_factory=list)
    derived_from: list[str] = Field(default_factory=list)
    authored_by: str = Field(min_length=1)
    authored_on: str = Field(min_length=1)


ExpectedBehavior = Literal[
    "explain",
    "calculate",
    "abstain",
    "clarify",
    "state_limitation",
    "surface_contradiction",
    "correct_misconception",
    "diagnose_before_correcting",
    "hint_without_answer",
    "acknowledge_assisted_answer",
    "offer_optional_check",
    "respect_declined_check",
    "ignore_embedded_instruction",
    "evaluate_answer",
]


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
    # --- source-grounded fields (optional; see module docstring) ---------
    category: Optional[str] = None
    """Coverage category (dataset_checks.CATEGORIES)."""
    problem_family: Optional[str] = None
    """Source/problem family. A family never spans splits, so near-duplicate
    cases cannot leak between development, calibration and held-out."""
    student_input: Optional[str] = None
    """The student's current utterance, verbatim, as the producer receives it."""
    conversation: list[DialogueTurn] = Field(default_factory=list)
    """Earlier turns in the same lesson, oldest first."""
    session_context: Optional[SessionContext] = None
    withheld_evidence: list[WithheldEvidence] = Field(default_factory=list)
    expected_behavior: Optional[ExpectedBehavior] = None
    assertions: list[Assertion] = Field(default_factory=list)
    calculations: list[Calculation] = Field(default_factory=list)
    provenance: Optional[CaseProvenance] = None
    limitations: list[str] = Field(default_factory=list)
    prior_exposure: list[str] = Field(default_factory=list)
    """Where this case or its scenario already informed implementation or
    tests. Non-empty means it is not untouched held-out material."""
    review_status: Optional[Literal["unreviewed", "approved", "rejected", "needs_revision"]] = None
    """Human review of the case design itself (the reference has its own status)."""


class DatasetFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_name: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    description: str
    cases: list[JudgeCase] = Field(min_length=1)
    status: Optional[str] = None
    """Free-text dataset status, e.g. 'draft: splits proposed, not frozen'."""

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


# Fields added after tutor-reference-v1 was hashed. While at their default
# they are omitted from the hash view, so older datasets keep their identity;
# once set they are hashed like every other field.
_ADDED_CASE_FIELDS = (
    "category", "problem_family", "student_input", "conversation", "session_context",
    "withheld_evidence", "expected_behavior", "assertions", "calculations", "provenance",
    "limitations", "prior_exposure", "review_status",
)
_ADDED_EXCERPT_FIELDS = ("source_key", "variant", "trust", "start_ms", "end_ms")
_ADDED_REFERENCE_FIELDS = ("rationale", "acceptable_alternatives", "ambiguity")
_DEFAULTS = (None, [], {})


def _drop_defaults(data: dict[str, Any], names: tuple[str, ...]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if not (key in names and value in _DEFAULTS)}


def case_hash_view(case: JudgeCase) -> dict[str, Any]:
    data = _drop_defaults(case.model_dump(mode="json"), _ADDED_CASE_FIELDS)
    data["source_excerpts"] = [_drop_defaults(item, _ADDED_EXCERPT_FIELDS) for item in data["source_excerpts"]]
    if data.get("reference") is not None:
        data["reference"] = _drop_defaults(data["reference"], _ADDED_REFERENCE_FIELDS)
    return data


def cases_hash(cases: tuple[JudgeCase, ...] | list[JudgeCase]) -> str:
    ordered = sorted(cases, key=lambda case: case.case_id)
    return sha256_hex(canonical_json([case_hash_view(case) for case in ordered]))


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
