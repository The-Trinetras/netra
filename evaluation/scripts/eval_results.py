"""Ordinal judge results and explicit non-scores.

model-evaluation-plan.md: "Keep ordinal scores, provenance and
skipped/error outcomes in evaluation-owned structures; do not coerce them
into the existing boolean EvaluationResult without an explicit reviewed
mapping." This module is that structure. There is deliberately no mapping
to interfaces.EvaluationResult / CriterionScore.passed: an ordinal 1-5
judgement is not a pass, and nothing here invents a threshold.

Every unit of judge work — one (run, case, repetition, criterion) — ends
in exactly one CriterionResult whose ``outcome`` says what happened:

- SCORED: a strictly parsed integer 1..5. The only outcome with a score.
- NOT_APPLICABLE: the criterion does not apply to this case (declared in
  the dataset, never decided by the judge).
- MISSING: no judgement exists and none was attempted or completed —
  pending human reference, budget exhausted before dispatch, run stopped.
- INVALID: the judge answered, but not in the required form (no or
  duplicate [RESULT], out of range, truncated, malformed).
- FAILED: the call itself failed (timeout, 429, auth, OOM, credits, 5xx).

Aggregations must report these counts beside any score distribution.
Missing, invalid and failed are neither zero nor passing.
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Outcome(str, Enum):
    SCORED = "scored"
    NOT_APPLICABLE = "not_applicable"
    MISSING = "missing"
    INVALID = "invalid"
    FAILED = "failed"


class ErrorCode(str, Enum):
    """Safe, enumerated reasons. Never a raw exception or provider payload."""

    # MISSING
    REFERENCE_PENDING = "reference_pending"
    BUDGET_EXHAUSTED = "budget_exhausted"
    RUN_STOPPED = "run_stopped"
    # INVALID (judge output)
    NO_RESULT_MARKER = "no_result_marker"
    DUPLICATE_RESULT_MARKER = "duplicate_result_marker"
    NON_INTEGER_RESULT = "non_integer_result"
    OUT_OF_RANGE = "out_of_range"
    TRAILING_CONTENT = "trailing_content"
    TRUNCATED = "truncated"
    EMPTY_FEEDBACK = "empty_feedback"
    INVALID_PREFERENCE = "invalid_preference"
    # FAILED (call)
    TIMEOUT_UNCERTAIN = "timeout_uncertain"
    RATE_LIMITED = "rate_limited"
    AUTH_FAILED = "auth_failed"
    CREDIT_EXHAUSTED = "credit_exhausted"
    OUT_OF_MEMORY = "out_of_memory"
    OVERSIZED_INPUT = "oversized_input"
    SERVER_ERROR = "server_error"
    PROTOCOL_ERROR = "protocol_error"


_OUTCOME_CODES = {
    Outcome.MISSING: {ErrorCode.REFERENCE_PENDING, ErrorCode.BUDGET_EXHAUSTED, ErrorCode.RUN_STOPPED},
    Outcome.INVALID: {
        ErrorCode.NO_RESULT_MARKER,
        ErrorCode.DUPLICATE_RESULT_MARKER,
        ErrorCode.NON_INTEGER_RESULT,
        ErrorCode.OUT_OF_RANGE,
        ErrorCode.TRAILING_CONTENT,
        ErrorCode.TRUNCATED,
        ErrorCode.EMPTY_FEEDBACK,
        ErrorCode.INVALID_PREFERENCE,
    },
    Outcome.FAILED: {
        ErrorCode.TIMEOUT_UNCERTAIN,
        ErrorCode.RATE_LIMITED,
        ErrorCode.AUTH_FAILED,
        ErrorCode.CREDIT_EXHAUSTED,
        ErrorCode.OUT_OF_MEMORY,
        ErrorCode.OVERSIZED_INPUT,
        ErrorCode.SERVER_ERROR,
        ErrorCode.PROTOCOL_ERROR,
    },
}


class UnitKey(BaseModel):
    """Stable identity of one unit of judge work."""

    model_config = ConfigDict(frozen=True)

    run_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    repetition: int = Field(ge=0)
    criterion_id: str = Field(min_length=1)
    """A versioned evaluation name, e.g. ``source_support_v1``."""

    def as_str(self) -> str:
        return f"{self.run_id}/{self.case_id}/r{self.repetition}/{self.criterion_id}"


class CriterionResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    key: UnitKey
    outcome: Outcome
    score: Optional[int] = Field(default=None, ge=1, le=5)
    feedback: Optional[str] = Field(default=None, max_length=8000)
    """The judge's concise feedback, stored as evidence for review. Never
    private chain of thought; never exported to ordinary logs."""
    error_code: Optional[ErrorCode] = None
    judge_config_id: str
    input_hash: str
    """Cache identity: hash of every input and configuration value that
    could change the judgement (see judge_input_hash)."""
    request_id: Optional[str] = None
    """The judge request identity, for reconciling uncertain completion."""
    elapsed_ms: Optional[int] = Field(default=None, ge=0)
    reused_from: Optional[str] = None
    """Set when this result was copied from an identical-input result in
    another run. Reuse is explicit and always labelled."""

    @model_validator(mode="after")
    def _consistent(self) -> "CriterionResult":
        if self.outcome is Outcome.SCORED:
            if self.score is None or self.error_code is not None:
                raise ValueError("a scored result has a score and no error code")
        else:
            if self.score is not None:
                raise ValueError(f"a {self.outcome.value} result carries no score")
            if self.outcome is not Outcome.NOT_APPLICABLE:
                if self.error_code is None or self.error_code not in _OUTCOME_CODES[self.outcome]:
                    raise ValueError(f"a {self.outcome.value} result needs a matching error code")
            elif self.error_code is not None:
                raise ValueError("a not-applicable result carries no error code")
        return self


def canonical_json(value: Any) -> str:
    """Deterministic JSON: sorted keys, no whitespace variance, UTF-8 kept."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def judge_input_hash(**parts: Any) -> str:
    """Hash of every input to one judgement. All parts are required by the
    caller; adding a part changes every hash, which is the point."""

    return sha256_hex(canonical_json(parts))
