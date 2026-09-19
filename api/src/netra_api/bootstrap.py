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
    LearningTutorRunner,
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
import os

from netra_api.platform.observability import (
    FanOutTraceSink,
    InMemoryTraceSink,
    LoggingTraceSink,
    TraceSink,
    TracingTraceSink,
)
from netra_api.platform.tracing import ExportSettings, SpanExporter, Tracer, build_tracer, disable_langsmith_export
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
    tutor_services: Any = None
    """M4's ``netra_api.learning.tutor.agent.TutorServices``. When supplied (and
    tutor_runner is not), it is wrapped in LearningTutorRunner and its
    ``pending_questions`` repository is used for navigation and resume."""
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
    engine: Any = None
    """The shared async engine when a database is configured (one pool per process)."""


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
    tracer: Optional[Tracer] = None
    langsmith_flags_overridden: list[str] = field(default_factory=list)
    shutdown_timeout_seconds: float = 5.0
    sources: Any = None
    """M2 SourceRepository (account-scoped) for the source-listing route."""

    def telemetry_diagnostics(self) -> dict[str, Any]:
        """Safe counters only: no identifiers, messages or configuration values."""

        tracer = self.tracer
        snapshot = tracer.diagnostics.snapshot() if tracer is not None else {}
        return {
            "tracing_enabled": bool(tracer is not None and tracer.enabled),
            "langsmith_export_disabled": True,
            **snapshot,
        }

    def shutdown(self) -> None:
        """Bounded final flush at orderly process shutdown; never per turn."""

        if self.tracer is not None:
            self.tracer.shutdown(self.shutdown_timeout_seconds)


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
        engine=engine,
    )


def production_dependencies(engine: Any, tracer: Optional[Tracer] = None,
                            settings: Optional[Settings] = None) -> IntegrationDependencies:
    """Real M2 content services and M4's durable learning store over one engine.

    Every M2 repository is built around one AsyncSession, so each is wrapped in
    SessionScoped: every call gets its own session and short transactions,
    never a process-wide shared session. Retrieval degrades to PostgreSQL
    full-text search when the Gemini/Pinecone providers are not configured and
    reports RetrievalUnavailableError only when both paths fail.

    Provider-backed agents (Coordinator model, Tutor provider) and speech are
    added by the caller only when their adapters are configured; absent ones
    stay unregistered and their requests fail explicitly.
    """

    from netra_api.content.reading.postgres import AsyncReadingBlockRepository
    from netra_api.content.reading.postgres_positions import AsyncReadingPositionRepository
    from netra_api.content.retrieval.factory import build_postgres_retrieval_service
    from netra_api.content.retrieval.postgres_evidence import AsyncPostgresEvidenceResolver
    from netra_api.content.settings import ContentSettings
    from netra_api.content.sources.postgres import AsyncSourceRepository
    from netra_api.db.scoped import SessionScoped
    from netra_api.learning.postgres import PostgresLearningStore
    from netra_api.platform.database import create_session_factory

    sessions = create_session_factory(engine)
    content = ContentSettings()
    learning = PostgresLearningStore(sessions)
    dependencies = IntegrationDependencies(
        reading_positions=SessionScoped(sessions, AsyncReadingPositionRepository),
        reading_blocks=SessionScoped(sessions, AsyncReadingBlockRepository),
        sources=SessionScoped(sessions, AsyncSourceRepository),
        evidence_resolver=SessionScoped(sessions, AsyncPostgresEvidenceResolver),
        retrieval=SessionScoped(sessions, lambda session: build_postgres_retrieval_service(session, content, tracer)),
        pending_questions=learning,
    )
    settings = settings or Settings()
    timeout = settings.model_request_timeout_seconds
    # A native adapter always wins; OpenRouter only fills a slot left empty.
    if settings.gemini_api_key is not None:
        from netra_api.coordinator.providers.gemini_client import GeminiCoordinatorAdapter

        dependencies.coordinator_model = GeminiCoordinatorAdapter.from_api_key(
            settings.gemini_api_key.get_secret_value(), timeout_seconds=timeout)
    elif settings.openrouter_api_key is not None:
        from netra_api.coordinator.providers.openrouter_client import OpenRouterCoordinatorAdapter

        dependencies.coordinator_model = OpenRouterCoordinatorAdapter.from_api_key(
            settings.openrouter_api_key.get_secret_value(), model_id=settings.openrouter_coordinator_model,
            timeout_seconds=timeout)
    tutor_provider = None
    if settings.groq_api_key is not None:
        from netra_api.learning.tutor.providers.groq_client import GroqTutorAdapter

        tutor_provider = GroqTutorAdapter.from_api_key(settings.groq_api_key.get_secret_value(), timeout_seconds=timeout)
    elif settings.openrouter_api_key is not None:
        from netra_api.learning.tutor.providers.openrouter_client import OpenRouterTutorAdapter

        tutor_provider = OpenRouterTutorAdapter.from_api_key(
            settings.openrouter_api_key.get_secret_value(), model_id=settings.openrouter_tutor_model,
            timeout_seconds=timeout)
    if tutor_provider is not None:
        from netra_api.learning.assessment.service import LearningService
        from netra_api.learning.tutor.agent import TutorServices

        # No quiz generator is registered: optional-check support (D2) is an
        # open decision and fails closed, so drafting questions is not wired.
        dependencies.tutor_services = TutorServices(
            provider=tutor_provider,
            evidence_resolver=dependencies.evidence_resolver,
            pending_questions=learning,
            learning_service=LearningService(learning, None, learning),
        )
    return dependencies


def compose(
    settings: Settings,
    repositories: Repositories,
    dependencies: IntegrationDependencies,
    *,
    trace_sink: Optional[TraceSink] = None,
    span_exporter: Optional[SpanExporter] = None,
    export_settings: Optional[ExportSettings] = None,
    environ: Any = None,
    tracer: Optional[Tracer] = None,
) -> Composition:
    langsmith_flags = disable_langsmith_export(os.environ if environ is None else environ)
    tracer = tracer or build_tracer(
        settings.tracing_mode,
        exporter=span_exporter,
        settings=export_settings or ExportSettings(),
        service_name="netra-api",
    )

    if dependencies.tutor_services is not None:
        if dependencies.tutor_runner is None:
            dependencies.tutor_runner = LearningTutorRunner(dependencies.tutor_services)
        if dependencies.pending_questions is None:
            dependencies.pending_questions = dependencies.tutor_services.pending_questions

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
            tracer=tracer,
        ).orchestrate_with_langgraph()

    if settings.auth_mode == "stored_credential" and settings.database_url:
        verifier: CredentialVerifier = StoredCredentialVerifier(repositories.identity)
    else:
        verifier = UnconfiguredCredentialVerifier()

    base_sink = trace_sink or (LoggingTraceSink() if settings.trace_to_log else InMemoryTraceSink())
    sink = FanOutTraceSink(base_sink, TracingTraceSink(tracer)) if tracer.enabled else base_sink
    if dependencies.speech_output is not None:
        dependencies.speech_output.tracer = tracer
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
        tracer=tracer,
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
    return Composition(
        services=services,
        verifier=verifier,
        registered=registered,
        tracer=tracer,
        langsmith_flags_overridden=langsmith_flags,
        shutdown_timeout_seconds=settings.tracing_shutdown_timeout_seconds,
        sources=dependencies.sources,
    )


def build_production(settings: Optional[Settings] = None, dependencies: Optional[IntegrationDependencies] = None) -> Composition:
    settings = settings or Settings()
    repositories = durable_repositories(settings)
    tracer = None
    if dependencies is None and repositories.engine is not None:
        tracer = build_tracer(settings.tracing_mode, settings=ExportSettings(), service_name="netra-api")
        dependencies = production_dependencies(repositories.engine, tracer, settings)
    return compose(settings, repositories, dependencies or IntegrationDependencies(), tracer=tracer)
