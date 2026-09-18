"""Engine-level checks with the LABELLED Ohm fixture: lecture repair, real tool adapters, delegation fencing.

The lecture is supplied as an explicit companion source version because no
committed session field yet records a PDF+lecture selection (open M3/M5/M1
decision). Real adapters (SearchSourcesTool, DescribeFigureTool,
SearchLectureTool) run against fixture M2/M3 services.
"""

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from netra_api.coordinator.context import ContextSelector
from netra_api.coordinator.decisions import TutorRequest
from netra_api.coordinator.evidence_check import EvidenceLedger
from netra_api.coordinator.graph import CoordinatorEngine
from netra_api.coordinator.limits import TurnBudget
from netra_api.coordinator.state import CoordinatorTurnState
from netra_api.coordinator.tool_registry import ToolContext, ToolEvidence, ToolRegistry
from netra_api.coordinator.tools import (
    DescribeFigureArgs,
    SearchLectureArgs,
    SearchSourcesArgs,
    register_available_tools,
    DescribeFigureTool,
    SearchLectureTool,
    SearchSourcesTool,
)
from netra_api.coordinator.tutor_gateway import TutorGateway
from netra_api.content.retrieval.evidence import EvidenceTrust
from netra_api.platform.auth_context import AuthContext
from netra_api.platform.errors import TurnCancelledError
from netra_api.platform.observability import InMemoryTraceSink, TurnTrace

from ohm_fixture import (
    ACCOUNT,
    AXES,
    LECTURE_V1,
    OHM_V1,
    SESSION,
    FixtureFigures,
    FixturePendingQuestions,
    FixtureResolver,
    FixtureRetrieval,
    FixtureTutor,
    FixtureVideo,
    ScriptedModel,
    final,
    initial_state,
    tools,
)


def _auth(account=ACCOUNT):
    return AuthContext(account_id=account, session_id=SESSION, request_id=uuid4(), issued_at=datetime.now(timezone.utc))


def _context(companions=frozenset(), budget=None):
    auth = _auth()
    sink = InMemoryTraceSink()
    return ToolContext(auth=auth, budget=budget or TurnBudget(), session=initial_state(), trace=TurnTrace(sink, request_id=auth.request_id, session_id=SESSION), companion_source_version_ids=companions)


async def test_lecture_transcript_without_axes_triggers_graph_retrieval_then_validation():
    model = ScriptedModel(
        [
            tools(("search_lecture", {"query": "this line", "lecture_source_version_id": str(LECTURE_V1), "start_ms": 42000, "end_ms": 58000}), requirements=AXES),
            tools(
                ("describe_figure", {"figure_index": 1}),
                assessments=[
                    {"requirement_id": "x-axis", "status": "missing", "evidence_id": "ev-lecture-0042", "gap": "axes not established from transcript"},
                    {"requirement_id": "y-axis", "status": "missing", "evidence_id": "ev-lecture-0042", "gap": "axes not established from transcript"},
                ],
            ),
            final(
                {
                    "action": "answer",
                    "assessments": [
                        {"requirement_id": "x-axis", "status": "supported", "evidence_id": "ev-fig02", "observation_label": "x-axis: current (A)"},
                        {"requirement_id": "y-axis", "status": "supported", "evidence_id": "ev-fig02", "observation_label": "y-axis: voltage (V)"},
                    ],
                    "text": "Figure 2 plots current on x and voltage on y; the straight line means a constant ratio.",
                    "cited_evidence_ids": ["ev-fig02", "ev-lecture-0042"],
                }
            ),
        ]
    )
    resolver = FixtureResolver()
    registry = ToolRegistry()
    register_available_tools(registry, resolver=resolver, figures=FixtureFigures(), video=FixtureVideo())
    engine = CoordinatorEngine(model=model, registry=registry, context=ContextSelector(None))
    sink = InMemoryTraceSink()
    auth = _auth()
    turn = CoordinatorTurnState(session_id=SESSION, request_id=auth.request_id, auth=auth, budget=TurnBudget(), original_utterance="How does this line show constant resistance?", session=initial_state(), companion_source_version_ids=frozenset({str(LECTURE_V1)}))

    outcome = await engine.run(turn, TurnTrace(sink, request_id=auth.request_id, session_id=SESSION))

    assert outcome.kind == "answer"
    assert outcome.segments[0].evidence_ids == ("ev-fig02", "ev-lecture-0042")
    results = [e.detail for e in sink.of_kind("tool_result")]
    assert results[0]["tool"] == "search_lecture" and results[1]["tool"] == "describe_figure"
    assert sink.of_kind("evidence_gap")[0].detail["evidence_id"] == "ev-lecture-0042"
    assert sink.of_kind("action_changed")[0].detail["reason_requirements"] == ["x-axis", "y-axis"]


async def test_transcript_can_never_be_accepted_as_a_visual_observation():
    from netra_api.coordinator.decisions import Assessment, Requirement

    tool = SearchLectureTool(FixtureVideo(), FixtureResolver())
    result = await tool(_context(frozenset({str(LECTURE_V1)})), SearchLectureArgs(query="line", lecture_source_version_id=str(LECTURE_V1)))
    ledger = EvidenceLedger()
    ledger.add_requirements((Requirement(requirement_id="x-axis", description="x axis"),))
    ledger.add_evidence(result.evidence)
    ledger.apply_assessments((Assessment(requirement_id="x-axis", status="supported", evidence_id="ev-lecture-0042", observation_label="x-axis"),))
    assert ledger.requirements["x-axis"].status == "missing"
    assert result.evidence[0].observations[0].label == "spoken transcript (not visual evidence)"


async def test_lecture_outside_the_turn_scope_is_refused():
    tool = SearchLectureTool(FixtureVideo(), FixtureResolver())
    result = await tool(_context(), SearchLectureArgs(query="line", lecture_source_version_id=str(LECTURE_V1)))
    assert result.evidence == () and result.rejected_count == 1


async def test_search_adapter_rejects_unauthorized_vector_hits_after_search():
    retrieval = FixtureRetrieval({"line": ["ev-passage-b12", "ev-other-student", "ev-lecture-0042"]})
    tool = SearchSourcesTool(retrieval, FixtureResolver())
    result = await tool(_context(), SearchSourcesArgs(query="this line"))
    assert [e.evidence_id for e in result.evidence] == ["ev-passage-b12"]
    assert result.rejected_count == 2  # another account's evidence and an unpinned version


async def test_figure_adapter_preserves_observation_provenance_and_authorizes():
    tool = DescribeFigureTool(FixtureFigures(readable=False), FixtureResolver())
    result = await tool(_context(), DescribeFigureArgs(figure_index=1))
    evidence = result.evidence[0]
    assert evidence.trust == EvidenceTrust.DERIVED
    assert {o.source.value for o in evidence.observations} == {"unreadable"}

    other = AuthContext(account_id=uuid4(), session_id=SESSION, request_id=uuid4(), issued_at=datetime.now(timezone.utc))
    context = _context()
    context.auth = other
    denied = await tool(context, DescribeFigureArgs(figure_index=1))
    assert denied.evidence == () and denied.rejected_count == 1


async def test_real_adapters_cannot_delegate_until_evidence_versions_exist():
    ledger = EvidenceLedger()
    ledger.add_evidence((ToolEvidence(evidence_id="ev", source_version_id=str(OHM_V1), locator="p", text="t", provenance="m2", trust=EvidenceTrust.SOURCE_VERIFIED),))
    pending = FixturePendingQuestions()
    gateway = TutorGateway(FixtureTutor(pending), pending_questions=pending)
    from netra_api.coordinator.tutor_gateway import HandoffRejectedError

    with pytest.raises(HandoffRejectedError) as raised:
        await gateway.delegate(auth=_auth(), session=initial_state(), request_id=uuid4(), utterance="explain", budget=TurnBudget(), ledger=ledger, request=TutorRequest(mode="explain", learning_goal="g", evidence_ids=("ev",)), trace=TurnTrace(InMemoryTraceSink(), request_id=uuid4(), session_id=SESSION))
    assert raised.value.check == "evidence_version_unavailable"


async def test_cancellation_during_tutor_delegation_returns_no_result():
    ledger = EvidenceLedger()
    ledger.add_evidence((ToolEvidence(evidence_id="ev", source_version_id=str(OHM_V1), evidence_version=1, locator="p", text="t", provenance="m2", trust=EvidenceTrust.SOURCE_VERIFIED),))
    pending = FixturePendingQuestions()
    tutor = FixtureTutor(pending, delay=5.0)
    gateway = TutorGateway(tutor, pending_questions=pending)
    budget = TurnBudget()
    delegation = asyncio.ensure_future(
        gateway.delegate(auth=_auth(), session=initial_state(), request_id=uuid4(), utterance="explain", budget=budget, ledger=ledger, request=TutorRequest(mode="explain", learning_goal="g", evidence_ids=("ev",)), trace=TurnTrace(InMemoryTraceSink(), request_id=uuid4(), session_id=SESSION))
    )
    await asyncio.sleep(0.05)
    budget.cancel()
    with pytest.raises(TurnCancelledError):
        await asyncio.wait_for(delegation, 1.0)
    assert tutor.budgets[0] is budget  # the Tutor was handed the originating instance, not a fresh one


async def test_real_adapters_carry_the_resolvers_evidence_version():
    # INT-03: the evidence version comes from the authorized resolver, never
    # from a fixture wrapper, so delegation can build a contract-valid ref.
    retrieval = FixtureRetrieval({"line": ["ev-passage-b12"]})
    search = await SearchSourcesTool(retrieval, FixtureResolver())(_context(), SearchSourcesArgs(query="this line"))
    figure = await DescribeFigureTool(FixtureFigures(readable=True), FixtureResolver())(_context(), DescribeFigureArgs(figure_index=1))
    lecture = await SearchLectureTool(FixtureVideo(), FixtureResolver())(
        _context(frozenset({str(LECTURE_V1)})), SearchLectureArgs(query="line", lecture_source_version_id=str(LECTURE_V1)))
    for result in (search, figure, lecture):
        assert result.evidence and all(item.evidence_version == 1 for item in result.evidence)


def test_langgraph_wiring_when_pinned_package_is_installed():
    pytest.importorskip("langgraph")
    from netra_api.coordinator.graph import build_langgraph

    engine = CoordinatorEngine(model=ScriptedModel([]), registry=ToolRegistry(), context=ContextSelector(None), instructions="x")
    assert build_langgraph(engine) is not None
