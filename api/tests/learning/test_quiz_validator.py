import pytest

from netra_api.learning.quiz.models import AnswerKey, QuestionDraft, QuestionKind, QuestionOption
from netra_api.learning.quiz.validator import QuestionValidationError, validate_question_draft


def _mc_draft(**overrides):
    defaults = dict(
        concept_id="concept-x",
        kind=QuestionKind.MULTIPLE_CHOICE,
        prompt="Which protocol is connection-oriented?",
        options=[QuestionOption(option_id="a", text="UDP"), QuestionOption(option_id="b", text="TCP")],
        answer_key=AnswerKey(correct_answer="b"),
    )
    defaults.update(overrides)
    return QuestionDraft(**defaults)


def test_valid_multiple_choice_draft_passes():
    validate_question_draft(_mc_draft())


def test_multiple_choice_without_options_is_rejected():
    with pytest.raises(QuestionValidationError):
        validate_question_draft(_mc_draft(options=[]))


def test_multiple_choice_with_duplicate_option_ids_is_rejected():
    draft = _mc_draft(options=[QuestionOption(option_id="a", text="UDP"), QuestionOption(option_id="a", text="TCP")])
    with pytest.raises(QuestionValidationError):
        validate_question_draft(draft)


def test_multiple_choice_correct_answer_must_reference_an_option():
    draft = _mc_draft(answer_key=AnswerKey(correct_answer="z"))
    with pytest.raises(QuestionValidationError):
        validate_question_draft(draft)


def test_free_response_without_rubric_is_rejected():
    draft = QuestionDraft(
        concept_id="concept-x",
        kind=QuestionKind.FREE_RESPONSE,
        prompt="Explain congestion control.",
        answer_key=AnswerKey(),
    )
    with pytest.raises(QuestionValidationError):
        validate_question_draft(draft)


def test_short_answer_with_rubric_passes():
    draft = QuestionDraft(
        concept_id="concept-x",
        kind=QuestionKind.SHORT_ANSWER,
        prompt="What does TCP stand for?",
        answer_key=AnswerKey(correct_answer="Transmission Control Protocol", rubric="Accept minor spelling errors."),
    )
    validate_question_draft(draft)


def test_empty_prompt_is_rejected():
    with pytest.raises(QuestionValidationError):
        validate_question_draft(_mc_draft(prompt="   "))


# --- D2 first half: evidence-reference binding ------------------------------

from uuid import uuid4  # noqa: E402

from netra_api.content.retrieval.evidence import Evidence, EvidenceTrust  # noqa: E402
from netra_api.learning.quiz.validator import (  # noqa: E402
    UngroundedDraftError,
    UngroundedDraftReason,
    bind_draft_to_evidence,
    evidence_refs_for,
    validate_question_for_approval,
)


def _evidence(evidence_id, trust=EvidenceTrust.SOURCE_VERIFIED):
    return Evidence(
        evidence_id=evidence_id,
        source_version_id=uuid4(),
        locator="p. 3",
        text="TCP is connection-oriented; UDP is not.",
        provenance="fixture",
        trust=trust,
    )


def test_binding_returns_the_cited_evidence_in_citation_order():
    a, b = _evidence("ev-a"), _evidence("ev-b")
    bound = bind_draft_to_evidence(_mc_draft(evidence_ids=["ev-b", "ev-a"]), [a, b])
    assert [item.evidence_id for item in bound] == ["ev-b", "ev-a"]


@pytest.mark.parametrize(
    ("evidence_ids", "reason"),
    [
        ([], UngroundedDraftReason.NO_CITATION),
        (["ev-a", "ev-a"], UngroundedDraftReason.DUPLICATE_CITATION),
        (["ev-a", "ev-invented"], UngroundedDraftReason.UNRESOLVED_CITATION),
    ],
)
def test_binding_refuses_drafts_that_do_not_cite_resolved_evidence(evidence_ids, reason):
    with pytest.raises(UngroundedDraftError) as refused:
        bind_draft_to_evidence(_mc_draft(evidence_ids=evidence_ids), [_evidence("ev-a")])
    assert refused.value.reason is reason
    assert isinstance(refused.value, QuestionValidationError)


def test_evidence_refs_keep_canonical_identity_and_trust_but_no_text():
    item = _evidence("ev-a", trust=EvidenceTrust.DERIVED)
    (ref,) = evidence_refs_for([item])
    assert ref.evidence_id == "ev-a"
    assert ref.source_version_id == item.source_version_id
    assert ref.trust is EvidenceTrust.DERIVED  # never upgraded
    assert "text" not in ref.model_dump()


def test_production_approval_still_fails_closed_after_binding_succeeds():
    """Binding is not grounding. A well-formed, correctly cited draft still
    stops at the unimplemented support check."""

    draft = _mc_draft(evidence_ids=["ev-a"])
    with pytest.raises(NotImplementedError):
        validate_question_for_approval(draft, [_evidence("ev-a")])


def test_production_approval_refuses_an_uncited_draft_before_the_support_check():
    with pytest.raises(UngroundedDraftError):
        validate_question_for_approval(_mc_draft(), [_evidence("ev-a")])
