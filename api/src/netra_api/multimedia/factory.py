"""Building M3's video tool and YouTube discovery from configuration.

Kept out of bootstrap.py for the same reason the retrieval factory is: this
function creates no engine, session factory or network connection. Provider
clients are constructed only for configured adapters when the factory runs, and an
unconfigured provider returns None so its capability reports itself
unregistered rather than failing at import (bootstrap.py's rule for every
integration).
"""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from netra_api.content.settings import ContentSettings
from netra_api.multimedia.discovery.tavily import TavilyDiscoverySettings, TavilyYouTubeDiscovery
from netra_api.multimedia.video.postgres import AsyncPostgresVideoEvidenceStore, PostgresCapabilityFacts
from netra_api.multimedia.video.stored_service import StoredVideoEvidenceService
from netra_api.platform.tracing import Tracer

_PINS = ("twelve_labs_index_id", "twelve_labs_marengo_model_name", "twelve_labs_marengo_model_version",
         "twelve_labs_pegasus_model_name", "twelve_labs_pegasus_model_version")


def twelve_labs_settings(settings: ContentSettings) -> Optional[Any]:
    """The adapter's configuration, or None when any pin is missing.

    M3-PIN-1: every model pin is required and none is inferred. A partially
    configured adapter would attribute evidence to a model that never ran, so
    an incomplete configuration is the same as no configuration.
    """

    if not settings.twelve_labs_api_key or any(not getattr(settings, name) for name in _PINS):
        return None
    from netra_api.multimedia.providers.twelve_labs_client import TwelveLabsSettings

    options = tuple(part.strip() for part in settings.twelve_labs_search_options.split(",") if part.strip())
    return TwelveLabsSettings(
        index_id=settings.twelve_labs_index_id,
        marengo_model_name=settings.twelve_labs_marengo_model_name,
        marengo_model_version=settings.twelve_labs_marengo_model_version,
        pegasus_model_name=settings.twelve_labs_pegasus_model_name,
        pegasus_model_version=settings.twelve_labs_pegasus_model_version,
        search_options=options,
        search_page_limit=settings.twelve_labs_search_page_limit,
        description_window_ms=settings.twelve_labs_description_window_ms,
        max_description_windows=settings.twelve_labs_max_description_windows,
        pegasus_max_tokens=settings.twelve_labs_pegasus_max_tokens,
        reconcile_max_pages=settings.twelve_labs_reconcile_max_pages,
        reconcile_page_limit=settings.twelve_labs_reconcile_page_limit,
        reconcile_timeout_seconds=settings.twelve_labs_reconcile_timeout_seconds,
    )


def build_video_evidence_service(session: AsyncSession, settings: Optional[ContentSettings] = None,
                                 tracer: Optional[Tracer] = None) -> StoredVideoEvidenceService:
    """The stored video evidence service for one request/session.

    Serves already-persisted evidence with no provider at all; a configured
    Twelve Labs adapter only adds Marengo semantic search within one video.
    Reading stored video evidence therefore keeps working when the provider is
    unconfigured or its key has expired.
    """

    settings = settings or ContentSettings()
    store = AsyncPostgresVideoEvidenceStore(session)
    marengo = build_video_search_provider(settings, tracer)
    return StoredVideoEvidenceService(
        store,
        # The resolver is the delivery gate: video candidates resolve as
        # DERIVED through the same canonical resolver as search chunks.
        _resolver(session),
        PostgresCapabilityFacts(session, provider=settings.video_provider,
                                embedded_player_available=True),
        provider=settings.video_provider,
        moment_before_ms=settings.video_moment_before_ms,
        moment_after_ms=settings.video_moment_after_ms,
        search_limit=settings.video_search_limit,
        search_timeout_seconds=settings.video_search_timeout_seconds,
        marengo=marengo,
        tracer=tracer,
    )


def build_video_search_provider(settings: ContentSettings,
                                tracer: Optional[Tracer] = None) -> Optional[Any]:
    """Configured Marengo search, independent of reads of stored evidence.

    Construction performs no provider request. Incomplete configuration or an
    unavailable SDK leaves search unregistered while already-stored evidence
    remains readable, including evidence at a captured player time.
    """

    from netra_api.multimedia.providers.twelve_labs_client import MarengoSearchAdapter, SdkTwelveLabsGateway

    try:
        configured = twelve_labs_settings(settings)
        if configured is None:
            return None
        gateway = SdkTwelveLabsGateway.from_api_key(settings.twelve_labs_api_key)
        return MarengoSearchAdapter(gateway, configured, tracer=tracer)
    except Exception:  # noqa: BLE001 - an unusable search provider stays unregistered
        return None


def _resolver(session: AsyncSession) -> Any:
    from netra_api.content.retrieval.postgres_evidence import AsyncPostgresEvidenceResolver

    return AsyncPostgresEvidenceResolver(session)


def build_youtube_resolver(settings: Optional[ContentSettings] = None,
                           tracer: Optional[Tracer] = None) -> Optional[Any]:
    """Tunelio, or None when no key is configured (M3-YT-ANALYSIS is off).

    None is the whole opt-out: with no resolver, a YouTube source can never be
    ingested, which is the documented behaviour rather than a silent failure
    later in the worker.
    """

    settings = settings or ContentSettings()
    if not settings.tunelio_api_key:
        return None
    from netra_api.multimedia.providers.tunelio import TunelioSettings, TunelioYouTubeResolver

    try:
        return TunelioYouTubeResolver.from_api_key(
            settings.tunelio_api_key,
            TunelioSettings(quality=settings.tunelio_quality,
                            timeout_seconds=settings.tunelio_timeout_seconds,
                            max_duration_seconds=settings.tunelio_max_duration_seconds,
                            max_bytes=settings.tunelio_max_bytes),
            tracer=tracer,
        )
    except Exception:  # noqa: BLE001 - an unusable provider stays unregistered
        return None


def build_discovery_provider(settings: Optional[ContentSettings] = None,
                             tracer: Optional[Tracer] = None) -> Optional[TavilyYouTubeDiscovery]:
    """Tavily YouTube discovery, or None when no key is configured."""

    settings = settings or ContentSettings()
    if not settings.tavily_api_key:
        return None
    if settings.tavily_search_depth not in ("basic", "advanced"):
        # An unknown depth is a configuration error, not a reason to pick one:
        # "advanced" costs more per search than "basic".
        return None
    try:
        return TavilyYouTubeDiscovery.from_api_key(
            settings.tavily_api_key,
            TavilyDiscoverySettings(search_depth=settings.tavily_search_depth,
                                    timeout_seconds=settings.tavily_timeout_seconds,
                                    request_max_results=settings.tavily_request_max_results),
            tracer=tracer,
        )
    except Exception:  # noqa: BLE001 - an unusable provider stays unregistered
        return None
