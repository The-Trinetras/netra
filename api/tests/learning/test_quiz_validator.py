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
    validate_draft_is_grounded,
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


def test_production_approval_accepts_a_draft_whose_answer_the_evidence_states():
    """P-1: TCP is the correct option and the evidence says so."""

    validate_question_for_approval(_mc_draft(evidence_ids=["ev-a"]), [_evidence("ev-a")])


def _grounding_refusal(draft, text):
    item = _evidence("ev-a").model_copy(update={"text": text})
    with pytest.raises(UngroundedDraftError) as caught:
        validate_draft_is_grounded(draft, [item])
    return caught.value.reason


def _short(prompt, answer):
    return _mc_draft(kind=QuestionKind.SHORT_ANSWER, options=[], prompt=prompt, answer_key=AnswerKey(correct_answer=answer))


def test_an_answer_the_evidence_does_not_state_is_refused():
    draft = _mc_draft(options=[QuestionOption(option_id="a", text="SCTP"), QuestionOption(option_id="b", text="UDP")], answer_key=AnswerKey(correct_answer="a"))
    assert _grounding_refusal(draft, "TCP is connection-oriented; UDP is not.") is UngroundedDraftReason.UNSUPPORTED_ANSWER


def test_words_from_different_sentences_or_the_opposite_polarity_do_not_support_a_claim():
    text = "TCP is connection-oriented; UDP is not."
    assert _grounding_refusal(_short("Describe UDP.", "UDP is connection-oriented"), text) is UngroundedDraftReason.UNSUPPORTED_ANSWER
    assert _grounding_refusal(_short("Is it constant?", "resistance is constant"), "Resistance is not constant for a diode.") is UngroundedDraftReason.UNSUPPORTED_ANSWER
    assert _grounding_refusal(_short("Is it constant?", "resistance is not constant"), "Resistance stays constant.") is UngroundedDraftReason.UNSUPPORTED_ANSWER


def test_numbers_must_match_exactly_in_the_supporting_sentence():
    text = "Table 4.1: at 2 A the voltage is 4 V. Resistance stays constant at 2 ohms."
    validate_draft_is_grounded(_short("What is the resistance in table 4.1?", "2 ohms"), [_evidence("ev-a").model_copy(update={"text": text})])
    assert _grounding_refusal(_short("What is the resistance in table 4.1?", "3 ohms"), text) is UngroundedDraftReason.UNSUPPORTED_ANSWER
    assert _grounding_refusal(_short("What is the resistance in table 4.2?", "2 ohms"), text) is UngroundedDraftReason.UNSUPPORTED_PROMPT


def test_a_question_about_something_else_is_refused_even_if_its_answer_appears():
    draft = _short("Who invented the transistor at Bell Labs in December?", "TCP")
    assert _grounding_refusal(draft, "TCP is connection-oriented; UDP is not.") is UngroundedDraftReason.UNSUPPORTED_PROMPT


def test_false_statements_and_empty_answers_are_uncheckable():
    true_false = [QuestionOption(option_id="t", text="True"), QuestionOption(option_id="f", text="False")]
    false_statement = _mc_draft(kind=QuestionKind.TRUE_FALSE, prompt="UDP is connection-oriented.", options=true_false, answer_key=AnswerKey(correct_answer="f"))
    assert _grounding_refusal(false_statement, "TCP is connection-oriented; UDP is not.") is UngroundedDraftReason.UNCHECKABLE
    true_statement = false_statement.model_copy(update={"prompt": "TCP is connection-oriented.", "answer_key": AnswerKey(correct_answer="t")})
    validate_draft_is_grounded(true_statement, [_evidence("ev-a")])
    symbolic = _short("Which protocol is connection-oriented?", "  ")
    assert _grounding_refusal(symbolic, "TCP is connection-oriented; UDP is not.") is UngroundedDraftReason.UNCHECKABLE


def test_free_response_is_asked_only_when_its_rubric_is_in_the_source():
    free = _mc_draft(kind=QuestionKind.FREE_RESPONSE, options=[], prompt="Explain why TCP is connection-oriented.", answer_key=AnswerKey(rubric="TCP is connection-oriented"))
    validate_draft_is_grounded(free, [_evidence("ev-a")])
    invented = free.model_copy(update={"answer_key": AnswerKey(rubric="mentions the three-way handshake and sequence numbers")})
    assert _grounding_refusal(invented, "TCP is connection-oriented; UDP is not.") is UngroundedDraftReason.UNSUPPORTED_ANSWER


def test_production_approval_refuses_an_uncited_draft_before_the_support_check():
    with pytest.raises(UngroundedDraftError):
        validate_question_for_approval(_mc_draft(), [_evidence("ev-a")])
