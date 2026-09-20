"""Quiz validation — a bounded tool, not the Tutor.

CLAUDE.md "Architecture: only two agents" explicitly lists "quiz
validation" as NOT an agent: it is deterministic, structural checking of
a Tutor-proposed QuestionDraft, run before the draft may become an
ApprovedQuestion (see netra_api.learning.quiz.repository). It never
judges whether a *student's* answer is correct — that is either
netra_api.learning.assessment.grader (deterministic, objective kinds)
or the Tutor itself (rubric-graded free text). This is pure, local
validation with no LLM call and no I/O, so it is implemented in full
rather than stubbed.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Optional, Sequence

from netra_api.content.retrieval.evidence import Evidence
from netra_api.learning.quiz.models import QuestionDraft, QuestionEvidenceRef, QuestionKind
from netra_api.platform.errors import NetraError

CHOICE_KINDS = frozenset({QuestionKind.MULTIPLE_CHOICE, QuestionKind.TRUE_FALSE})
RUBRIC_REQUIRED_KINDS = frozenset({QuestionKind.SHORT_ANSWER, QuestionKind.FREE_RESPONSE})


class QuestionValidationError(NetraError):
    """Raised when a QuestionDraft is not well-formed enough to approve."""


def validate_question_draft(draft: QuestionDraft) -> None:
    """Raise QuestionValidationError if draft is not ready to be approved.

    Checks are purely structural: presence of a prompt, unique options
    with a correct_answer that references one of them for choice kinds,
    and a grading rubric for kinds the Tutor must grade by judgment.
    """

    if not draft.prompt.strip():
        raise QuestionValidationError("prompt must not be empty")

    if draft.kind in CHOICE_KINDS:
        if not draft.options:
            raise QuestionValidationError(f"{draft.kind.value} requires at least one option")
        option_ids = [option.option_id for option in draft.options]
        if len(option_ids) != len(set(option_ids)):
            raise QuestionValidationError("option_id values must be unique")
        if draft.answer_key.correct_answer not in option_ids:
            raise QuestionValidationError(
                f"{draft.kind.value} answer_key.correct_answer must reference an option_id"
            )

    if draft.kind in RUBRIC_REQUIRED_KINDS and not draft.answer_key.rubric:
        raise QuestionValidationError(f"{draft.kind.value} requires answer_key.rubric for Tutor grading")


class UngroundedDraftReason(str, Enum):
    """Why a draft's evidence citations could not be bound.

    Internal diagnostics for logs and tests. Like EvidenceRejectionReason,
    never echoed to a student or into model context.
    """

    NO_CITATION = "no_citation"
    DUPLICATE_CITATION = "duplicate_citation"
    UNRESOLVED_CITATION = "unresolved_citation"
    """The draft names an id this turn did not resolve: never supplied,
    refused by the resolver, or dropped for a source-version mismatch."""
    UNSUPPORTED_ANSWER = "unsupported_answer"
    """The reference answer is not found in the cited evidence (P-1)."""
    UNSUPPORTED_PROMPT = "unsupported_prompt"
    """The question is not about the cited evidence (P-1)."""
    UNCHECKABLE = "uncheckable"
    """No reference answer the evidence could confirm, e.g. a "false"
    true/false statement: fail closed rather than guess (P-1)."""


class UngroundedDraftError(QuestionValidationError):
    """A draft's citations do not bind to this turn's authorized evidence."""

    def __init__(self, reason: UngroundedDraftReason) -> None:
        self.reason = reason
        super().__init__(f"question draft is not bound to authorized evidence: {reason.value}")


def bind_draft_to_evidence(draft: QuestionDraft, evidence: Sequence[Evidence]) -> list[Evidence]:
    """Return the authorized evidence a draft cites, in citation order.

    D2, first half — reference binding. learning.md: "Validate that
    questions and reference answers are supported by the selected
    evidence." A draft citing nothing, citing an item twice, or citing an
    id this turn did not resolve cannot be supported by any definition of
    "supported", so it is refused before the support check runs. This is
    deterministic, makes no model call and decides no product policy: it
    only enforces that each citation points at authorized content.

    ``evidence`` must be exactly what this turn resolved through the
    authorized resolver, after the Tutor's source-version check
    (netra_api.learning.tutor.agent._resolve_evidence).

    Binding never approves a question on its own. Passing it shows the
    draft cites authorized evidence, not that the evidence supports it;
    validate_draft_is_grounded (the second half) still decides that and
    still fails closed. Do not report binding as grounding.

    M3's gates (ValidationReport.is_source_verified, citable_tables,
    MomentEvidence.supports_visual_claim) decide whether derived
    multimedia becomes citable Evidence at all; the resolver only returns
    registered evidence, and nothing here upgrades DERIVED trust.
    """

    if not draft.evidence_ids:
        raise UngroundedDraftError(UngroundedDraftReason.NO_CITATION)
    if len(set(draft.evidence_ids)) != len(draft.evidence_ids):
        raise UngroundedDraftError(UngroundedDraftReason.DUPLICATE_CITATION)

    by_id = {item.evidence_id: item for item in evidence}
    bound: list[Evidence] = []
    for evidence_id in draft.evidence_ids:
        item = by_id.get(evidence_id)
        if item is None:
            raise UngroundedDraftError(UngroundedDraftReason.UNRESOLVED_CITATION)
        bound.append(item)
    return bound


def evidence_refs_for(bound: Sequence[Evidence]) -> list[QuestionEvidenceRef]:
    """Canonical identities of bound evidence, for storing with a question."""

    return [
        QuestionEvidenceRef(
            evidence_id=item.evidence_id,
            source_version_id=item.source_version_id,
            trust=item.trust,
        )
        for item in bound
    ]


_WORD = re.compile(r"[a-z0-9]+(?:[.,][0-9]+)*%?")
_SENTENCE_BREAK = re.compile(r"(?<![0-9])[.!?;](?![0-9])|\n")
_NUMBER = re.compile(r"[0-9]")
_STOPWORDS = frozenset(
    "a an the of to in on at by for from with and or as is are was were be been being it its this that "
    "these those which what who whom whose when where why how do does did can could should would will "
    "shall may might must has have had i you he she we they them his her their our your there here "
    "than then so such into onto about over under between each any all both some".split()
)
_NEGATIONS = frozenset({"not", "no", "never", "none", "neither", "nor", "cannot"})
ANSWER_COVERAGE = 0.8
"""Share of the reference answer's content words the evidence must contain."""
PROMPT_COVERAGE = 0.5
"""Share of the question's content words the evidence must contain."""


def _words(text: str) -> list[str]:
    words = []
    for word in _WORD.findall(text.lower().replace("n't", " not")):
        if len(word) > 3 and word.endswith("s") and word not in _STOPWORDS and not _NUMBER.search(word):
            word = word[:-1]  # plurals: "volts" matches "volt"
        words.append(word)
    return words


def _content(words: Sequence[str]) -> list[str]:
    return [w for w in words if w in _NEGATIONS or (w not in _STOPWORDS and (len(w) > 1 or _NUMBER.search(w)))]


def _covered(claim: str, vocabulary: set[str], coverage: float) -> bool:
    content = _content(_words(claim))
    if not content:
        return False
    numbers = [w for w in content if _NUMBER.search(w)]
    if any(n not in vocabulary for n in numbers):
        return False  # every number, unit-bearing or not, must appear exactly
    return sum(w in vocabulary for w in content) >= coverage * len(content)


def _stated_in_one_sentence(claim: str, sentences: Sequence[set[str]], coverage: float) -> bool:
    """One evidence sentence carries the claim: its numbers, enough of its
    words, and the same polarity ("UDP is not" never supports "UDP is")."""

    content = _content(_words(claim))
    negated = any(w in _NEGATIONS for w in content)
    return bool(content) and any(
        negated == any(w in _NEGATIONS for w in sentence) and _covered(claim, sentence, coverage)
        for sentence in sentences
    )


def _reference_answer(draft: QuestionDraft) -> Optional[str]:
    """The text a student's correct answer must match, or None if uncheckable."""

    key = draft.answer_key
    if draft.kind == QuestionKind.MULTIPLE_CHOICE:
        option = next((o for o in draft.options if o.option_id == key.correct_answer), None)
        return option.text if option else None
    if draft.kind == QuestionKind.TRUE_FALSE:
        # Only a true statement can be confirmed by finding it in the source;
        # a false one cannot be checked lexically, so it is not asked.
        chosen = next((o for o in draft.options if o.option_id == key.correct_answer), None)
        return draft.prompt if chosen is not None and chosen.text.strip().lower() == "true" else None
    if draft.kind == QuestionKind.SHORT_ANSWER:
        return key.correct_answer
    return key.rubric  # FREE_RESPONSE: the rubric's criteria must come from the source


def validate_draft_is_grounded(draft: QuestionDraft, evidence: Sequence[Evidence]) -> None:
    """Check that a draft question and its answer key are supported by evidence.

    D2, second half — support. Decision P-1 (20 September 2026): a check
    question is asked only when its answer is supported by the cited
    evidence; otherwise it is skipped (fail closed). Receives only the
    evidence the draft is bound to (bind_draft_to_evidence).

    Support is checked deterministically against the evidence text, with
    no model call:

    - the reference answer (the correct option, the short answer, the
      free-response rubric, or a true statement) must be stated in one
      evidence sentence: every number exactly, at least ANSWER_COVERAGE of
      its content words, and the same polarity (negated or not);
    - the question itself must be about the evidence (PROMPT_COVERAGE);
    - a question with no reference answer the evidence could confirm,
      such as a false true/false statement, is not asked.

    ponytail: lexical support, not semantic entailment. A correct answer
    the model paraphrased away from the source is skipped (a missed
    question, never a wrong one). A model-based entailment check is the
    upgrade if too many good questions are skipped.
    """

    sentences = [
        words for item in evidence for part in _SENTENCE_BREAK.split(item.text) if (words := set(_words(part)))
    ]
    vocabulary = set().union(*sentences)
    answer = _reference_answer(draft)
    if answer is None or not answer.strip():
        raise UngroundedDraftError(UngroundedDraftReason.UNCHECKABLE)
    if not _stated_in_one_sentence(answer, sentences, ANSWER_COVERAGE):
        raise UngroundedDraftError(UngroundedDraftReason.UNSUPPORTED_ANSWER)
    if not _covered(draft.prompt, vocabulary, PROMPT_COVERAGE):
        raise UngroundedDraftError(UngroundedDraftReason.UNSUPPORTED_PROMPT)


def validate_question_for_approval(draft: QuestionDraft, evidence: Sequence[Evidence]) -> None:
    """Run every check a draft must pass before it may become an ApprovedQuestion.

    Structural checks, then reference binding, then support. Callers must
    use this rather than validate_question_draft alone: a structurally
    valid question is not an approvable one.
    """

    validate_question_draft(draft)
    bound = bind_draft_to_evidence(draft, evidence)
    validate_draft_is_grounded(draft, bound)
