"""Hybrid retrieval: keyword rank, reciprocal rank fusion, and the case that motivated them.

The ranking functions are pure, so most of this needs no embedding model. The one test that
uses the real embeddings skips itself where they are not installed.
"""
from __future__ import annotations

import pytest

from demo.notes.corpus import note_texts
from demo.notes.retrieval import fuse, keyword_rank, tokens
from slice.retrieve import split

DOCS = {
    "ohms": "Ohm's law states that V = I x R. The resistor is ohmic when V / I stays constant.",
    "series": "In a series circuit the same current flows through every component.",
    "power": "Power is measured in watts. P = V x I, and P = I^2 x R for a resistor.",
}


# ----------------------------------------------------------- keyword ranking

def test_the_document_that_shares_the_distinctive_word_ranks_first():
    assert keyword_rank("how much power does a resistor use", DOCS)[0] == "power"


def test_documents_sharing_no_query_word_are_left_out_not_ranked_last():
    ranked = keyword_rank("watts", DOCS)
    assert ranked == ["power"], "a document with no matching word is not evidence of anything"


def test_an_empty_or_all_stopword_query_ranks_nothing():
    assert keyword_rank("", DOCS) == []
    assert keyword_rank("how does the of it", DOCS) == []
    assert keyword_rank("watts", {}) == []


def test_stopwords_are_dropped_and_case_is_folded():
    assert tokens("How Does the CURRENT flow?") == ["current", "flow"]


def test_a_rarer_word_counts_for_more_than_a_common_one():
    # 'current' is in three of four documents and repeated three times in one; 'watts' is in
    # one document, once. Without idf the repeated common word would win; with it, the rare
    # word that actually distinguishes a document must.
    docs = {"a": "current current current", "b": "watts", "c": "current", "d": "current"}
    assert keyword_rank("watts current", docs)[0] == "b"


# ------------------------------------------------------------------- fusion

def test_an_item_high_in_both_rankings_wins():
    assert fuse(["a", "b", "c"], ["a", "c", "b"])[0] == "a"


def test_fusion_keeps_items_that_only_one_ranking_found():
    assert set(fuse(["a", "b"], ["c"])) == {"a", "b", "c"}


def test_ties_are_broken_by_the_first_ranking():
    assert fuse(["x", "y"], ["y", "x"]) == ["x", "y"]


def test_a_single_ranking_passes_through_unchanged():
    assert fuse(["a", "b", "c"]) == ["a", "b", "c"]


def test_the_failure_that_motivated_this_keyword_rank_rescues_a_buried_embedding_hit():
    """Measured with the real embedding model: the power note was ranked FIFTH of five.
    Here the embedding order is faked to put it last; fusion must lift it into the top three."""
    embedding_order = ["series", "ohms", "other-1", "other-2", "power"]
    lexical = keyword_rank("How much power does a 5 ohm resistor use?",
                           {**DOCS, "other-1": "unrelated text about voltage", "other-2": "more unrelated text"})
    assert lexical[0] == "power"
    assert "power" in fuse(embedding_order, lexical)[:3]


# ---------------------------------------------------- with the real embeddings

def test_the_real_embedding_model_plus_keywords_finds_the_power_note_for_the_power_question(tmp_path):
    pytest.importorskip("sqlite_vec")
    pytest.importorskip("fastembed")
    from demo.notes.corpus import ingest_notes, search_notes
    from slice.store import Store

    store = Store(str(tmp_path / "n.db"))
    ingest_notes(store)
    hits = search_notes(store, "How much power does a 5 ohm resistor use when 2 A flows through it?", k=3)
    assert "electrical-power-notes.md" in [h.doc for h in hits]


def test_searching_an_empty_corpus_returns_nothing_rather_than_inventing(tmp_path):
    pytest.importorskip("sqlite_vec")
    from demo.notes.corpus import search_notes
    from slice.store import Store

    assert search_notes(Store(str(tmp_path / "empty.db")), "anything", k=3) == []
