"""Tutor trace facts: allowlisted and content-free (AX data minimization).

These prove what M4 hands M1's tracing boundary. They do not prove
export, redaction by M1's pipeline, context isolation or AX ingestion;
none of that exists until M1's tracing boundary is published (INT-12).
"""

from netra_api.learning.quiz.models import AnswerKey, QuestionKind
from netra_api.learning.tutor.runner import TutorRunner
from netra_api.learning.tutor.trace_facts import TUTOR_TRACE_ATTRIBUTE_KEYS, tutor_trace_attributes

from test_tutor_agent import (
    _FakePendingQuestions,
    _FakeProvider,
    _answer_handoff,
    _auth,
    _inherited_budget,
    _live_handoff,
    _question,
    _services,
)

SECRETS = (
    "SECRET-UTTERANCE",
    "SECRET-DIALOGUE",
    "SECRET-GOAL",
    "SECRET-KEY",
    "SECRET-RUBRIC",
    "SECRET-FEEDBACK",
    "SECRET-PROMPT",
    "Congestion control limits the sending rate",  # evidence text from the resolver double
)


async def _facts(handoff, services):
    budget = _inherited_budget(handoff)
    outcome = await TutorRunner(services).run(handoff, _auth(handoff), budget)
    return tutor_trace_attributes(handoff, outcome, budget)


def _assert_content_free(facts):
    assert set(facts) == TUTOR_TRACE_ATTRIBUTE_KEYS
    for value in facts.values():
        assert isinstance(value, (str, int, bool))
        for secret in SECRETS:
            assert secret not in str(value)


async def test_an_explanation_turn_exports_only_allowlisted_facts():
    handoff = _live_handoff(
        mode="explain",
        original_utterance="SECRET-UTTERANCE",
        learning_goal="SECRET-GOAL",
        recent_dialogue=[{"role": "student", "content": "SECRET-DIALOGUE"}],
    )
    facts = await _facts(handoff, _services(provider=_FakeProvider(raw_text="SECRET-FEEDBACK")))

    _assert_content_free(facts)
    assert facts["netra.tutor.mode"] == "explain"
    assert facts["netra.tutor.status"] == "completed"
    assert facts["netra.tutor.evidence_ids"] == "ev-27"
    assert facts["netra.tutor.uncommitted_event_types"] == "concept_exposed"
    assert facts["netra.tutor.model_decisions_used"] == 1


async def test_a_graded_answer_exports_neither_the_answer_nor_the_key_or_feedback():
    question = _question(
        kind=QuestionKind.SHORT_ANSWER,
        options=[],
        prompt="SECRET-PROMPT",
        answer_key=AnswerKey(correct_answer="SECRET-KEY", rubric="SECRET-RUBRIC"),
    )
    services = _services(
        pending_questions=_FakePendingQuestions([question]),
        provider=_FakeProvider(raw_text="CORRECT\nSECRET-FEEDBACK"),
    )
    handoff = _answer_handoff("SECRET-UTTERANCE")

    facts = await _facts(handoff, services)

    _assert_content_free(facts)
    assert facts["netra.tutor.committed_event_types"] == "answer_evaluated"
    assert facts["netra.tutor.pending_question_change"] == "answered"
    assert facts["netra.tutor.had_pending_question"] is True
