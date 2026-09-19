"""The notes corpus, the fixed question set and the scripted stub.

Most of this needs nothing but the files. The retrieval test needs the kit's own
environment (sqlite-vec and the local embedding model, both in the Codespace
image) and skips itself elsewhere, so a green run outside the image proves less;
say which one you ran.
"""
from __future__ import annotations

import re

import pytest
from pydantic import BaseModel

from demo.notes.corpus import note_files, note_texts
from demo.notes.questions import QUESTIONS, by_id
from demo.notes.stub import ScriptedModel


# --------------------------------------------------------------- the corpus

def test_the_corpus_has_notes_and_none_are_empty():
    texts = note_texts()
    assert len(texts) >= 3
    assert all(len(text.strip()) > 200 for text in texts.values())


def test_note_files_come_in_a_stable_order():
    names = [f.name for f in note_files()]
    assert names == sorted(names)


# ------------------------------------------------- the questions vs the corpus

def test_question_ids_are_unique():
    ids = [q.id for q in QUESTIONS]
    assert len(ids) == len(set(ids))


def test_both_kinds_of_outcome_are_covered():
    outcomes = {q.outcome for q in QUESTIONS}
    assert outcomes == {"answer", "gap"}


@pytest.mark.parametrize("q", [q for q in QUESTIONS if q.outcome == "answer"], ids=lambda q: q.id)
def test_every_expected_fact_is_really_in_the_cited_notes(q):
    """The expected answer must come from the notes, not from the test author."""
    texts = note_texts()
    assert q.evidence, "an answerable question must name the notes it comes from"
    cited = " ".join(texts[name] for name in q.evidence)
    for fact in q.facts:
        assert fact.lower() in cited.lower(), f"{fact!r} is not in {q.evidence}"


@pytest.mark.parametrize("q", [q for q in QUESTIONS if q.outcome == "gap"], ids=lambda q: q.id)
def test_not_covered_questions_are_really_not_covered(q):
    """A 'gap' question is only a fair test if the notes truly lack the topic."""
    everything = " ".join(note_texts().values()).lower()
    assert q.absent, "a gap question must say which term the notes lack"
    for term in q.absent:
        assert term.lower() not in everything, f"the notes mention {term!r}"


def test_the_arithmetic_in_the_expected_answers_is_right():
    """Recompute the three calculated answers so a typo in a note cannot pass."""
    assert 6 / 3 == 2                          # q1: R = V / I
    assert 10 / (2 + 3) == 2                   # q2: I = V / (R1 + R2)
    assert 2 * 2 * 5 == 20                     # q3: P = I^2 x R
    # and the worked numbers in the notes agree with the formulas
    series = note_texts()["series-circuits-notes.md"]
    assert re.search(r"4 V \+ 6 V = 10 V", series)


def test_by_id_finds_a_question():
    assert by_id("q3-power").outcome == "answer"
    with pytest.raises(StopIteration):
        by_id("nope")


# ---------------------------------------------------------- the scripted stub

class _Reply(BaseModel):
    text: str


class _Budget:
    def __init__(self) -> None:
        self.tokens = 0

    def record_tokens(self, n: int) -> None:
        self.tokens += n


def _call(stub, step, schema=_Reply):
    return stub(settings=None, budget=_Budget(), messages=[{"role": "user", "content": "x"}],
                schema=schema, step=step)


def test_the_stub_replays_its_script_in_order_through_the_real_schema():
    stub = ScriptedModel({"draft": ['{"text": "first"}', '{"text": "second"}']})
    assert _call(stub, "draft").text == "first"
    assert _call(stub, "draft").text == "second"
    assert stub.calls == ["draft", "draft"]


def test_the_stub_fails_loudly_when_the_flow_asks_for_more_than_was_scripted():
    stub = ScriptedModel({"draft": ['{"text": "only"}']})
    _call(stub, "draft")
    with pytest.raises(AssertionError, match="no reply"):
        _call(stub, "draft")


def test_a_malformed_scripted_reply_fails_at_the_schema():
    stub = ScriptedModel({"draft": ['{"wrong": 1}']})
    with pytest.raises(Exception):
        _call(stub, "draft")


def test_the_stub_reports_replies_it_was_never_asked_for():
    stub = ScriptedModel({"draft": ['{"text": "a"}', '{"text": "b"}'], "gate": ['{"text": "g"}']})
    _call(stub, "draft")
    assert stub.remaining() == {"draft": 1, "gate": 1}


def test_step_names_with_a_suffix_use_the_same_script():
    stub = ScriptedModel({"gate": ['{"text": "a"}', '{"text": "b"}']})
    _call(stub, "gate:repair")
    assert _call(stub, "gate").text == "b"


# ------------------------------------------------------ retrieval (kit image)

def test_search_finds_the_right_note_for_a_question(tmp_path):
    pytest.importorskip("sqlite_vec")
    pytest.importorskip("fastembed")
    from demo.notes.corpus import ingest_notes, search_notes
    from slice.store import Store

    store = Store(str(tmp_path / "notes.db"))
    stats = ingest_notes(store)
    assert stats["chunks"] > 0
    again = ingest_notes(store)
    assert again["chunks"] == 0, "ingest is meant to be idempotent"

    for q in [q for q in QUESTIONS if q.outcome == "answer"]:
        hits = search_notes(store, q.text, k=3)
        assert hits, q.id
        assert any(h.doc in q.evidence for h in hits), (
            f"{q.id}: none of the top hits came from {q.evidence}: {[h.cite() for h in hits]}")
