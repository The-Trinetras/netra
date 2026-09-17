"""Composition root: wires M1 services with teammates' implementations.

Rules:
- Production composition registers only durable repositories (PostgreSQL) and
  only services that are actually supplied. In-memory repositories are never
  used here.
- A dependency that is not supplied is left unregistered and reported by
  ``Composition.registered``; the requests needing it fail explicitly
  (RESOURCE_UNAVAILABLE / PROVIDER_UNAVAILABLE), while independent paths such
  as deterministic navigation keep working.
- Teammates hand implementations in through ``IntegrationDependencies``; M1
  does not re-implement their modules to make a demo look complete.

Registration instructions for each boundary are in docs/team/handoffs/M1.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Optional

from netra_api.config import Settings
from netra_api.coordinator.context import ContextSelector
from netra_api.coordinator.graph import CoordinatorEngine
from netra_api.coordinator.providers.gemini import GeminiCoordinatorProvider
from netra_api.coordinator.tool_registry import ToolRegistry
from netra_api.coordinator.tools import register_available_tools
from netra_api.coordinator.tutor_gateway import (
    AssessmentSummaryProvider,
    LearningProposalSink,
    TutorGateway,
    TutorRunner,
)
from netra_api.identity.service import (
    CredentialVerifier,
    IdentityService,
    StoredCredentialVerifier,
    UnconfiguredCredentialVerifier,
)
from netra_api.platform.errors import ResourceUnavailableError
from netra_api.platform.observability import InMemoryTraceSink, LoggingTraceSink, TraceSink
from netra_api.session.navigation import DeterministicNavigator
from netra_api.session.reading import ReadingAccess
from netra_api.session.service import SessionService
from netra_api.speech.playback_metadata import GenerationRegistry
from netra_api.speech.synthesis import SpeechOutput
from netra_api.transport.websocket.dispatcher import TransportServices, TurnRegistry


@dataclass
class IntegrationDependencies:
    """Implementations supplied by other workstreams. Anything None stays unregistered."""

    # M2 content
    reading_positions: Any = None
    reading_blocks: Any = None
    sources: Any = None
    retrieval: Any = None
    evidence_resolver: Any = None
    # M3 multimedia
    figures: Any = None
    video_evidence: Any = None
    # M4 learning
    tutor_runner: Optional[TutorRunner] = None
    pending_questions: Any = None
    assessment_summaries: Optional[AssessmentSummaryProvider] = None
    learning_sink: Optional[LearningProposalSink] = None
    # M1 providers (behind adapters)
    coordinator_model: Optional[GeminiCoordinatorProvider] = None
    speech_output: Optional[SpeechOutput] = None


@dataclass
class Repositories:
    identity: Any
    sessions: Any
    result_sets: Any = None
    dialogue: Any = None


class UnavailableRepository:
    """Stands in when no database is configured: every call fails closed."""

    def __getattr__(self, name: str) -> Any:
        async def _unavailable(*args: Any, **kwargs: Any) -> Any:
            raise ResourceUnavailableError("persistence is not configured")

        return _unavailable


@dataclass
class Composition:
    services: TransportServices
    verifier: CredentialVerifier
    registered: dict[str, bool] = field(default_factory=dict)


def durable_repositories(settings: Settings) -> Repositories:
    if not settings.database_url:
        unavailable = UnavailableRepository()
        return Repositories(identity=unavailable, sessions=unavailable)

    from netra_api.identity.postgres import PostgresIdentityRepository
    from netra_api.platform.database import create_engine
    from netra_api.session.postgres import PostgresDialogueLog, PostgresResultSetRepository, PostgresSessionRepository

    engine = create_engine(settings.database_url)
    return Repositories(
        identity=PostgresIdentityRepository(engine),
        sessions=PostgresSessionRepository(engine),
        result_sets=PostgresResultSetRepository(engine),
        dialogue=PostgresDialogueLog(engine),
    )


def compose(
    settings: Settings,
    repositories: Repositories,
    dependencies: IntegrationDependencies,
    *,
    trace_sink: Optional[TraceSink] = None,
) -> Composition:
    reading = None
    if dependencies.reading_positions and dependencies.reading_blocks and dependencies.sources:
        reading = ReadingAccess(dependencies.reading_positions, dependencies.reading_blocks, dependencies.sources)

    ttl = timedelta(seconds=settings.result_set_ttl_seconds) if settings.result_set_ttl_seconds else None
    sessions = SessionService(
        repositories.sessions,
        reading=reading,
        result_sets=repositories.result_sets,
        result_set_ttl=ttl,
    )
    navigator = DeterministicNavigator(reading, dependencies.pending_questions, repositories.dialogue)

    registry = ToolRegistry()
    tools = register_available_tools(
        registry,
        resolver=dependencies.evidence_resolver,
        retrieval=dependencies.retrieval,
        figures=dependencies.figures,
        video=dependencies.video_evidence,
    )

    tutor = None
    if dependencies.tutor_runner is not None and dependencies.pending_questions is not None:
        tutor = TutorGateway(
            dependencies.tutor_runner,
            pending_questions=dependencies.pending_questions,
            dialogue=repositories.dialogue,
            summaries=dependencies.assessment_summaries,
            learning_sink=dependencies.learning_sink,
        )

    coordinator = None
    if dependencies.coordinator_model is not None:
        coordinator = CoordinatorEngine(
            model=dependencies.coordinator_model,
            registry=registry,
            context=ContextSelector(repositories.dialogue),
            tutor=tutor,
        )

    if settings.auth_mode == "stored_credential" and settings.database_url:
        verifier: CredentialVerifier = StoredCredentialVerifier(repositories.identity)
    else:
        verifier = UnconfiguredCredentialVerifier()

    sink = trace_sink or (LoggingTraceSink() if settings.trace_to_log else InMemoryTraceSink())
    services = TransportServices(
        identity=IdentityService(repositories.identity),
        sessions=sessions,
        navigator=navigator,
        generations=GenerationRegistry(),
        turns=TurnRegistry(),
        trace_sink=sink,
        coordinator=coordinator,
        dialogue=repositories.dialogue,
        speech=dependencies.speech_output,
    )
    registered = {
        "persistence": not isinstance(repositories.sessions, UnavailableRepository),
        "authentication": not isinstance(verifier, UnconfiguredCredentialVerifier),
        "reading": reading is not None,
        "coordinator_model": coordinator is not None,
        "tutor": tutor is not None,
        "speech": dependencies.speech_output is not None,
        "result_sets": ttl is not None and repositories.result_sets is not None,
        **{f"tool:{name}": True for name in tools},
    }
    return Composition(services=services, verifier=verifier, registered=registered)


def build_production(settings: Optional[Settings] = None, dependencies: Optional[IntegrationDependencies] = None) -> Composition:
    settings = settings or Settings()
    return compose(settings, durable_repositories(settings), dependencies or IntegrationDependencies())
