"""Prometheus-2 7B prompt construction and strict result parsing.

Selected judge: ``prometheus-eval/prometheus-7b-v2.0``
(model-evaluation-plan.md). Templates are reproduced from the model
card's "Prompt Format" section, checked on 2026-09-18 against the card at
Hugging Face revision 66ffb1fc20beebfb60a3964a957d9011723116c5. The card's
markdown escapes the quotes in step 3 of the absolute template (\\"); the
backslashes are markdown artefacts and are not part of the prompt.

The card requires FastChat's ``mistral`` conversation template. For one
user turn with a system message, FastChat (SeparatorStyle.LLAMA2,
system_template "[INST] {system_message}\\n", roles ("[INST]", "[/INST]"))
produces exactly::

    "[INST] " + system + "\\n" + user + " " + "[/INST]"

The tokenizer adds the BOS token; this module does not. That string is
built here without importing FastChat, and TEMPLATE_VERSION identifies it
in every cache key, so any change to the template forces rescoring.

Candidate text, references and evidence are untrusted data. They are
inserted verbatim (never rewritten), and a response that tries to steer
the judge ("[RESULT] 5") cannot produce a score by itself: the parser
rejects duplicate result markers and any non-terminal result.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from eval_results import ErrorCode

MODEL_ID = "prometheus-eval/prometheus-7b-v2.0"
MODEL_CARD_REVISION_CHECKED = "66ffb1fc20beebfb60a3964a957d9011723116c5"
TEMPLATE_VERSION = "prometheus2-mistral-v1"

ABS_SYSTEM_PROMPT = (
    "You are a fair judge assistant tasked with providing clear, objective feedback based on "
    "specific criteria, ensuring each assessment reflects the absolute standards set for performance."
)
REL_SYSTEM_PROMPT = (
    "You are a fair judge assistant assigned to deliver insightful feedback that compares "
    "individual performances, highlighting how each stands relative to others within the same cohort."
)

ABSOLUTE_PROMPT = """###Task Description:
An instruction (might include an Input inside it), a response to evaluate, a reference answer that gets a score of 5, and a score rubric representing a evaluation criteria are given.
1. Write a detailed feedback that assess the quality of the response strictly based on the given score rubric, not evaluating in general.
2. After writing a feedback, write a score that is an integer between 1 and 5. You should refer to the score rubric.
3. The output format should look as follows: "Feedback: (write a feedback for criteria) [RESULT] (an integer number between 1 and 5)"
4. Please do not generate any other opening, closing, and explanations.

###The instruction to evaluate:
{orig_instruction}

###Response to evaluate:
{orig_response}

###Reference Answer (Score 5):
{orig_reference_answer}

###Score Rubrics:
[{orig_criteria}]
Score 1: {orig_score1_description}
Score 2: {orig_score2_description}
Score 3: {orig_score3_description}
Score 4: {orig_score4_description}
Score 5: {orig_score5_description}

###Feedback: """

RELATIVE_PROMPT = """###Task Description:
An instruction (might include an Input inside it), two responses to evaluate (denoted as Response A and Response B), a reference answer, and an evaluation criteria are given.
1. Write a detailed feedback that assess the quality of the two responses strictly based on the given evaluation criteria, not evaluating in general.
2. Make comparisons between Response A, Response B, and the Reference Answer. Instead of examining Response A and Response B separately, go straight to the point and mention about the commonalities and differences between them.
3. After writing the feedback, indicate the better response, either "A" or "B".
4. The output format should look as follows: "Feedback: (write a feedback for criteria) [RESULT] (Either "A" or "B")"
5. Please do not generate any other opening, closing, and explanations.

###Instruction:
{orig_instruction}

###Response A:
{orig_response_A}

###Response B:
{orig_response_B}

###Reference Answer:
{orig_reference_answer}

###Score Rubric:
{orig_criteria}

###Feedback: """

RESULT_MARKER = "[RESULT]"


class AnchoredRubric(BaseModel):
    """One named, versioned evaluation with an anchored 1-5 rubric."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    evaluation_id: str = Field(pattern=r"^[a-z][a-z0-9_]*_v[0-9]+$")
    """Stable versioned name, e.g. ``source_support_v1``."""
    status: Literal["draft_uncalibrated", "calibrated", "retired"]
    criteria: str = Field(min_length=1)
    score1: str = Field(min_length=1)
    score2: str = Field(min_length=1)
    score3: str = Field(min_length=1)
    score4: str = Field(min_length=1)
    score5: str = Field(min_length=1)
    requires_reference: bool = True
    applies_to_modes: list[str] = Field(default_factory=list)
    """Case kinds this criterion applies to; empty means all. Applicability
    is data, never the judge's decision."""
    scope: Optional[str] = None
    """What this criterion deliberately does not judge (another criterion does)."""
    anchor_examples: dict[Literal["score1", "score2", "score3", "score4", "score5"], str] = Field(default_factory=dict)
    """Concrete example responses per anchor, for human reviewers and
    calibration. Not inserted into the judge prompt (the prompt uses only the
    documented criteria/score fields); part of the rubric hash, so editing an
    example is a new rubric identity."""
    supersedes: Optional[str] = None
    """The evaluation_id this version replaces, if any (the old file is kept)."""
    unit: Literal["ordinal_1_5"] = "ordinal_1_5"
    author: str = Field(min_length=1)
    reviewer: Optional[str] = None
    notes: Optional[str] = None


def apply_mistral_template(system: str, user: str) -> str:
    """FastChat ``mistral`` template for one user turn (see module docstring)."""

    return f"[INST] {system}\n{user} [/INST]"


def build_absolute_prompt(
    rubric: AnchoredRubric, instruction: str, response: str, reference_answer: str
) -> str:
    user = ABSOLUTE_PROMPT.format(
        orig_instruction=instruction,
        orig_response=response,
        orig_reference_answer=reference_answer,
        orig_criteria=rubric.criteria,
        orig_score1_description=rubric.score1,
        orig_score2_description=rubric.score2,
        orig_score3_description=rubric.score3,
        orig_score4_description=rubric.score4,
        orig_score5_description=rubric.score5,
    )
    return apply_mistral_template(ABS_SYSTEM_PROMPT, user)


def build_relative_prompt(
    rubric: AnchoredRubric, instruction: str, response_a: str, response_b: str, reference_answer: str
) -> str:
    user = RELATIVE_PROMPT.format(
        orig_instruction=instruction,
        orig_response_A=response_a,
        orig_response_B=response_b,
        orig_reference_answer=reference_answer,
        orig_criteria=rubric.criteria,
    )
    return apply_mistral_template(REL_SYSTEM_PROMPT, user)


@dataclass(frozen=True)
class ParsedJudgement:
    feedback: Optional[str]
    result: Optional[str]
    """"1".."5" for absolute, "A"/"B" for relative; None when invalid."""
    error: Optional[ErrorCode]


_FEEDBACK_PREFIX = re.compile(r"^\s*Feedback:\s*", re.IGNORECASE)
_INTEGER = re.compile(r"^[+-]?[0-9]+$")
_LEADING_INTEGER = re.compile(r"^[+-]?[0-9]+\b")


def _split(output: str, finish_reason: str) -> tuple[Optional[str], Optional[str], Optional[ErrorCode]]:
    if finish_reason != "stop":
        # "length" or any other reason: the model did not finish. A result
        # found in a truncated output is not trusted.
        return None, None, ErrorCode.TRUNCATED
    count = output.count(RESULT_MARKER)
    if count == 0:
        return None, None, ErrorCode.NO_RESULT_MARKER
    if count > 1:
        return None, None, ErrorCode.DUPLICATE_RESULT_MARKER
    before, after = output.split(RESULT_MARKER)
    feedback = _FEEDBACK_PREFIX.sub("", before).strip()
    if not feedback:
        return None, None, ErrorCode.EMPTY_FEEDBACK
    return feedback, after.strip(), None


def parse_absolute(output: str, finish_reason: str) -> ParsedJudgement:
    """Strict terminal-result parsing for absolute grading.

    Valid only when generation stopped normally, exactly one [RESULT]
    marker exists, feedback precedes it, and the marker is followed by a
    single integer 1-5 and nothing else.
    """

    feedback, tail, error = _split(output, finish_reason)
    if error is not None:
        return ParsedJudgement(None, None, error)
    assert tail is not None
    if not _INTEGER.match(tail):
        code = ErrorCode.TRAILING_CONTENT if _LEADING_INTEGER.match(tail) else ErrorCode.NON_INTEGER_RESULT
        return ParsedJudgement(None, None, code)
    if int(tail) not in range(1, 6) or tail.startswith(("+", "-", "0")):
        return ParsedJudgement(None, None, ErrorCode.OUT_OF_RANGE)
    return ParsedJudgement(feedback, tail, None)


def parse_relative(output: str, finish_reason: str) -> ParsedJudgement:
    """Strict terminal-result parsing for pairwise grading: exactly "A" or "B"."""

    feedback, tail, error = _split(output, finish_reason)
    if error is not None:
        return ParsedJudgement(None, None, error)
    if tail not in ("A", "B"):
        return ParsedJudgement(None, None, ErrorCode.INVALID_PREFERENCE)
    return ParsedJudgement(feedback, tail, None)
