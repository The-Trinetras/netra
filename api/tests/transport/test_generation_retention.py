"""The generation registry keeps a bounded number of sessions.

Every navigation creates a generation record and every app launch opens a new
session, so a long-running API process must release idle sessions. Releasing
is safe only because an unknown generation is ineligible, and only for
sessions with nothing active or paused: STOP must always reach active audio,
and a paused generation must survive to be resumed.
"""

from uuid import uuid4

from netra_api.speech.playback_metadata import DeliveredSentence, GenerationRegistry


def _played(registry, session_id):
    generation = registry.start(session_id, uuid4())
    registry.record_sentence(generation, DeliveredSentence(segment_id="seg", sentence_id="s1", origin="generated"))
    registry.complete(generation)
    return generation


def test_idle_sessions_beyond_the_cap_are_released_least_recent_first():
    registry = GenerationRegistry(max_sessions=3)
    sessions = [uuid4() for _ in range(5)]
    generations = [_played(registry, session) for session in sessions]
    assert registry.retained_sessions == 3
    assert registry.get(sessions[0], generations[0].generation_id) is None
    assert registry.get(sessions[1], generations[1].generation_id) is None
    assert all(registry.get(s, g.generation_id) is g for s, g in zip(sessions[2:], generations[2:]))


def test_a_session_that_starts_a_new_generation_becomes_most_recent():
    registry = GenerationRegistry(max_sessions=3)
    a, b, c, d = (uuid4() for _ in range(4))
    for session in (a, b, c):
        _played(registry, session)
    latest_a = _played(registry, a)
    _played(registry, d)
    assert registry.get(a, latest_a.generation_id) is latest_a
    assert registry.retained_sessions == 3 and registry.paused(b) is None


def test_sessions_with_active_or_paused_playback_are_never_released():
    registry = GenerationRegistry(max_sessions=2)
    speaking = uuid4()
    active = registry.start(speaking, uuid4())
    pausing = uuid4()
    paused = _played(registry, pausing)
    assert registry.pause_active(pausing) is paused
    for _ in range(5):
        _played(registry, uuid4())
    assert registry.get(speaking, active.generation_id) is active
    assert registry.paused(pausing) is paused
    assert registry.cancel_speaking(speaking, "user_stop") == [active]  # STOP still reaches it


def test_a_released_sessions_generation_is_unknown_and_so_ineligible():
    registry = GenerationRegistry(max_sessions=1)
    old = uuid4()
    generation = _played(registry, old)
    _played(registry, uuid4())
    assert registry.lookup_delivered(old, generation.generation_id, "seg", "s1") is None
    assert registry.cancel_generation(old, generation.generation_id, "user_stop") is False
