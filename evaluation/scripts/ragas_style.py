"""Repository-owned Ragas-style retrieval metrics (Ragas stays uninstalled).

runtime-baseline.md / learning.md: implement Ragas-style metrics in
evaluation scripts instead of adding the Ragas package. These are
deterministic set/rank metrics over evidence ids, requiring human
relevant-evidence labels. Without labels the result is NOT_EVALUATED,
never 0 or 1. They measure retrieval of labelled-relevant evidence, not
factual correctness, and are not comparable with Prometheus ordinal scores.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Sequence


@dataclass(frozen=True)
class MetricValue:
    name: str
    value: Optional[float]
    evaluated: bool
    reason: Optional[str] = None


def _not_evaluated(name: str, reason: str) -> MetricValue:
    return MetricValue(name=name, value=None, evaluated=False, reason=reason)


def context_precision(retrieved: Sequence[str], relevant: Optional[set[str]]) -> MetricValue:
    """Mean of precision@k over the ranks k where a relevant item appears
    (the rank-aware form Ragas describes), normalised by relevant hits."""

    name = "context_precision"
    if relevant is None:
        return _not_evaluated(name, "no_relevance_labels")
    if not retrieved:
        return _not_evaluated(name, "nothing_retrieved")
    hits = 0
    total = 0.0
    for rank, evidence_id in enumerate(retrieved, start=1):
        if evidence_id in relevant:
            hits += 1
            total += hits / rank
    return MetricValue(name=name, value=(total / hits) if hits else 0.0, evaluated=True)


def context_recall(retrieved: Sequence[str], relevant: Optional[set[str]]) -> MetricValue:
    name = "context_recall"
    if relevant is None:
        return _not_evaluated(name, "no_relevance_labels")
    if not relevant:
        return _not_evaluated(name, "empty_relevance_label_set")
    return MetricValue(name=name, value=len(set(retrieved) & relevant) / len(relevant), evaluated=True)


def cited_evidence_support(cited: Sequence[str], supporting: Optional[set[str]]) -> MetricValue:
    """Share of cited evidence ids a human marked as supporting the answer
    (a faithfulness-style proxy; a citation alone never proves support)."""

    name = "cited_evidence_support"
    if supporting is None:
        return _not_evaluated(name, "no_support_labels")
    if not cited:
        return _not_evaluated(name, "nothing_cited")
    return MetricValue(name=name, value=sum(1 for e in cited if e in supporting) / len(cited), evaluated=True)


# ---------------------------------------------------------------------------
# Answer-side metrics
#
# Ragas computes these with an LLM and embeddings. Ragas stays uninstalled, and
# no judge call belongs in a metric, so these are deterministic LEXICAL proxies
# over content words: they detect an answer that talks about something the
# retrieved context never mentions, or that ignores the question. They do not
# establish factual correctness, and they are not comparable with a Ragas run or
# with Prometheus ordinal scores. Report them as Ragas-style proxies.
# ---------------------------------------------------------------------------

_STOPWORDS = frozenset(
    "a an the this that these those is are was were be been being do does did doing have has had of in on at to for "
    "from by with about as into over after before between and or but if then than so such not no nor it its they them "
    "their there here what which who whom when where why how can could should would may might will just also very".split()
)

SUPPORT_THRESHOLD = 0.6
"""Share of a sentence's content words that must appear in the context for the
sentence to count as supported. A tuning knob, not a product threshold."""


def _stem(word: str) -> str:
    """Crude suffix stripping so create/creates and learn/learning match.

    ponytail: a lexical proxy cannot see paraphrase ("underlying structure" vs
    "patterns"), so a genuinely supported sentence can still score low. Raising
    that ceiling means embeddings or a judge, which is Prometheus-2's job, not
    this metric's.
    """

    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def _content_words(text: str) -> set[str]:
    return {_stem(word) for word in re.findall(r"[a-z0-9]+", text.lower())
            if word not in _STOPWORDS and len(word) > 1}


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+|\n+", text) if part.strip()]


def faithfulness(answer: Optional[str], contexts: Sequence[str]) -> MetricValue:
    """Share of answer sentences whose content words the retrieved context
    covers (a Ragas-style lexical support proxy, not entailment).

    Unsupported here means "the context does not contain these words", which is
    evidence of an ungrounded claim, never proof of one; and a supported
    sentence can still be wrong.
    """

    name = "faithfulness"
    if not answer or not answer.strip():
        return _not_evaluated(name, "no_answer")
    if not contexts:
        return _not_evaluated(name, "no_retrieved_context")
    context_words = set().union(*(_content_words(context) for context in contexts)) if contexts else set()
    sentences = [sentence for sentence in _sentences(answer) if _content_words(sentence)]
    if not sentences:
        return _not_evaluated(name, "answer_has_no_content_words")
    supported = 0
    for sentence in sentences:
        words = _content_words(sentence)
        if len(words & context_words) / len(words) >= SUPPORT_THRESHOLD:
            supported += 1
    return MetricValue(name=name, value=supported / len(sentences), evaluated=True)


def answer_relevancy(question: Optional[str], answer: Optional[str]) -> MetricValue:
    """Share of the question's content words the answer addresses (a Ragas-style
    lexical proxy for Ragas' generated-question cosine form).

    A low value means the answer talks about something else; a high value only
    means it uses the question's terms, not that the answer is correct.
    """

    name = "answer_relevancy"
    if not question or not question.strip():
        return _not_evaluated(name, "no_question")
    if not answer or not answer.strip():
        return _not_evaluated(name, "no_answer")
    asked = _content_words(question)
    if not asked:
        return _not_evaluated(name, "question_has_no_content_words")
    return MetricValue(name=name, value=len(asked & _content_words(answer)) / len(asked), evaluated=True)
