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

from enum import Enum
from typing import Sequence

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


def validate_draft_is_grounded(draft: QuestionDraft, evidence: Sequence[Evidence]) -> None:
    """Check that a draft question and its answer key are supported by evidence.

    D2, second half — support. Receives only the evidence the draft is
    already bound to (bind_draft_to_evidence). Structural validation and
    binding cannot establish support: a draft can be well-formed, cite
    authorized evidence, and still ask about something the source never
    said, or carry a reference answer the evidence contradicts. Approving
    it would put a fabricated claim in front of a student as an
    assessable fact.

    Left unimplemented deliberately. What counts as "supported" is a
    product decision spanning M3 and M4 that has not been made: whether
    grounding is checked by entailment against the evidence text, by a
    Tutor self-check, or by requiring a human-reviewed question bank.
    Each implies a different pipeline, and guessing one would make it
    policy by default.

    Fails closed: until that decision exists, nothing can be approved
    through this path (CLAUDE.md: "Unimplemented authorization or
    persistence must fail closed, never return success").
    """

    raise NotImplementedError(
        "TODO: quiz evidence grounding — no approved definition of evidence support exists yet"
    )


def validate_question_for_approval(draft: QuestionDraft, evidence: Sequence[Evidence]) -> None:
    """Run every check a draft must pass before it may become an ApprovedQuestion.

    Structural checks, then reference binding, then support. Callers must
    use this rather than validate_question_draft alone: a structurally
    valid question is not an approvable one. Fails closed at the support
    step until the D2 decision is implemented.
    """

    validate_question_draft(draft)
    bound = bind_draft_to_evidence(draft, evidence)
    validate_draft_is_grounded(draft, bound)
