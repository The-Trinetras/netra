"""The answer-side Ragas-style metrics and how a result carries all four.

These are deterministic lexical proxies (ragas_style), not a Ragas run and not
a judge. The rules under test are the ones that make them safe to report:
missing inputs are NOT_EVALUATED rather than 0.0, and every average travels
with the number of examples it was actually computed on.
"""

from __future__ import annotations

from ragas_style import answer_relevancy, faithfulness
from retrieval_evaluation import RetrievalEvaluationResult, aggregate_metrics, with_ragas_style_metrics


def result(**overrides) -> RetrievalEvaluationResult:
    base = dict(
        experiment_id="exp-1", experiment_version="v1", dataset_id="ds-1", dataset_version="v1",
        example_id="case-1", retrieval_strategy="hybrid", query="How does resistance affect current?",
        retrieval_status="success", latency_ms=1.0,
    )
    base.update(overrides)
    return RetrievalEvaluationResult(**base)


# --- faithfulness ----------------------------------------------------------


def test_faithfulness_is_not_evaluated_without_an_answer_or_context():
    assert faithfulness(None, ("some context",)).evaluated is False
    assert faithfulness("", ("some context",)).reason == "no_answer"
    assert faithfulness("An answer.", ()).reason == "no_retrieved_context"
    assert faithfulness("An answer.", ()).value is None


def test_faithfulness_counts_the_sentences_the_context_covers():
    contexts = ("Ohm's law states that current equals voltage divided by resistance.",)
    both = faithfulness("Current equals voltage divided by resistance. The kettle boiled quickly today.", contexts)
    assert both.evaluated is True and both.value == 0.5


def test_a_fully_grounded_answer_scores_one_and_an_unrelated_answer_scores_zero():
    contexts = ("Current equals voltage divided by resistance.",)
    assert faithfulness("Current equals voltage divided by resistance.", contexts).value == 1.0
    assert faithfulness("Bake the bread for forty minutes.", contexts).value == 0.0


# --- answer relevancy ------------------------------------------------------


def test_answer_relevancy_is_not_evaluated_without_a_question_or_answer():
    assert answer_relevancy(None, "An answer.").reason == "no_question"
    assert answer_relevancy("A question?", None).reason == "no_answer"
    assert answer_relevancy("The and of?", "An answer.").reason == "question_has_no_content_words"


def test_answer_relevancy_measures_which_of_the_asked_terms_the_answer_addresses():
    # asked: resistance, affect, current ("how"/"does" are stopwords)
    partial = answer_relevancy("How does resistance affect current?", "Resistance reduces current.")
    assert partial.evaluated is True and partial.value == 2 / 3
    assert answer_relevancy("How does resistance affect current?",
                            "Resistance does affect current: it reduces it.").value == 1.0
    assert answer_relevancy("How does resistance affect current?", "The recipe needs butter.").value == 0.0


# --- carried on the result -------------------------------------------------


def test_a_retrieval_only_result_gets_context_metrics_and_leaves_answer_metrics_unevaluated():
    scored = with_ragas_style_metrics(result(
        retrieved_chunk_ids=("e1", "e9", "e2"), reference_evidence_ids=("e1", "e2")))
    assert scored.context_recall == 1.0
    assert scored.context_precision == (1 / 1 + 2 / 3) / 2  # e1 at rank 1, e2 at rank 3
    assert scored.faithfulness is None and scored.answer_relevancy is None


def test_without_relevance_labels_the_context_metrics_stay_none_rather_than_zero():
    scored = with_ragas_style_metrics(result(retrieved_chunk_ids=("e1", "e2")))
    assert scored.context_precision is None and scored.context_recall is None


def test_a_result_that_carries_an_answer_gets_all_four():
    scored = with_ragas_style_metrics(result(
        retrieved_chunk_ids=("e1",), reference_evidence_ids=("e1",),
        retrieved_contexts=("Resistance reduces the current in a circuit.",),
        generated_answer="Resistance does affect the current: it reduces it."))
    assert scored.context_precision == 1.0 and scored.context_recall == 1.0
    assert scored.faithfulness == 1.0 and scored.answer_relevancy == 1.0


def test_the_aggregate_reports_the_denominator_each_average_was_computed_on():
    scored = [
        with_ragas_style_metrics(result(example_id="a", retrieved_chunk_ids=("e1",), reference_evidence_ids=("e1",))),
        with_ragas_style_metrics(result(example_id="b", retrieved_chunk_ids=("e3",))),  # unlabelled
    ]
    aggregated = aggregate_metrics(scored)
    assert aggregated.examples == 2
    assert aggregated.context_recall == 1.0
    assert aggregated.evaluated["context_recall"] == 1
    assert aggregated.evaluated["faithfulness"] == 0
    assert aggregated.faithfulness is None
