"""Hybrid retrieval: embedding rank + keyword rank, fused with reciprocal rank fusion.

Ported from the earlier project's retrieval design (lexical and semantic search fused with
RRF). Why it is here: measured on this corpus with the kit's embedding model (bge-small),
embeddings alone ranked the power note FIFTH of five for "How much power does a 5 ohm
resistor use when 2 A flows through it?", so a drafter given the top three passages never
saw it. A small embedding model blurs short technical notes together; the word "power" is
a strong signal it misses. Keyword rank catches it, and fusion keeps what embeddings do
well (paraphrases with no shared words).

The ranking functions are pure so they can be tested without a model: `keyword_rank` and
`fuse` take plain lists. Only `hybrid_search` touches the run database.
"""
from __future__ import annotations

import math
import re
from collections import Counter

RRF_K = 60
"""The usual reciprocal-rank-fusion constant. It damps the influence of top ranks so that
neither ranking dominates just because it was confident."""

_STOP = frozenset("""a an and are as at be by can do does for from how i if in into is it its of on or
that the their there these this to was what when where which who why will with you your through
use used uses much many""".split())


def tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", text.casefold()) if t not in _STOP]


def keyword_rank(query: str, docs: dict[str, str], k1: float = 1.5, b: float = 0.75) -> list[str]:
    """Doc ids by BM25 score, best first. Docs with no query term are left out, not ranked
    last: a document that shares no word with the question is not evidence of anything."""
    q_terms = set(tokens(query))
    if not q_terms or not docs:
        return []
    tokenized = {doc_id: tokens(text) for doc_id, text in docs.items()}
    n = len(tokenized)
    avg_len = sum(len(t) for t in tokenized.values()) / n or 1.0
    df = Counter(term for toks in tokenized.values() for term in set(toks))

    scores: dict[str, float] = {}
    for doc_id, toks in tokenized.items():
        tf = Counter(toks)
        score = 0.0
        for term in q_terms:
            if tf[term] == 0:
                continue
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            score += idf * tf[term] * (k1 + 1) / (tf[term] + k1 * (1 - b + b * len(toks) / avg_len))
        if score > 0:
            scores[doc_id] = score
    return sorted(scores, key=lambda d: (-scores[d], d))


def fuse(*rankings: list[str], k: int = RRF_K) -> list[str]:
    """Reciprocal rank fusion: each ranking gives an item 1 / (k + rank). Ties keep the order
    of the first ranking, so the embedding order is the tiebreak."""
    scores: dict[str, float] = {}
    first_seen: dict[str, int] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
            first_seen.setdefault(item, len(first_seen))
    return sorted(scores, key=lambda i: (-scores[i], first_seen[i]))


def hybrid_search(store, query: str, k: int = 3, allowed: set[str] | None = None):
    """The k best passages for a query, by embedding rank fused with keyword rank.

    `allowed` is the set of passage ids the asker may see (None means no restriction, for the built-in
    demonstration). It is applied BEFORE any ranking, so a passage the asker may not see cannot even
    influence the order of one they may. An empty set returns nothing."""
    from slice.retrieve import corpus_size, search

    total = corpus_size(store)
    if total == 0:
        return []
    by_vector = search(store, query, k=total)                 # the whole index, ranked
    if allowed is not None:
        by_vector = [c for c in by_vector if c.chunk_id in allowed]
    if not by_vector:
        return []
    chunks = {c.chunk_id: c for c in by_vector}
    fused = fuse([c.chunk_id for c in by_vector],
                 keyword_rank(query, {cid: c.text for cid, c in chunks.items()}))
    return [chunks[cid] for cid in fused[:k]]
