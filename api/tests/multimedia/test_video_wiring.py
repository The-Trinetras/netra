"""Wiring for M3 video: the canonical asset table, the DERIVED evidence path
and the composition that decides whether video is registered at all.

The adapters and the video service were already written and tested; nothing
constructed them, so ``IntegrationDependencies.video_evidence`` stayed None and
every video request was unregistered. These cover the pieces that close that
gap: migration 0012, the PostgreSQL read side, the resolver's DERIVED branch
and the factory's fail-closed configuration rules.

The database-backed tests are marked integration and skip without
NETRA_TEST_DATABASE_URL, matching test_d1_foundation.py. The rest use no
database at all.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import delete, insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from netra_api.content.retrieval.evidence import EvidenceRejectionReason, EvidenceTrust
from netra_api.content.retrieval.postgres_evidence import AsyncPostgresEvidenceResolver
from netra_api.content.settings import ContentSettings
from netra_api.db.models import (SourceRow, SourceVersionRow, VideoAssetRow,
                                 VideoEvidenceCandidateRow, VideoProviderBindingRow)
from netra_api.multimedia.factory import build_discovery_provider, twelve_labs_settings
from netra_api.multimedia.video.models import VideoEvidenceKind, VideoSourceKind
from netra_api.multimedia.video.postgres import AsyncPostgresVideoEvidenceStore, PostgresCapabilityFacts
from netra_api.multimedia.video.readiness import AnalysisStage
from netra_api.platform.auth_context import AuthContext
from netra_api.platform.database import create_engine, create_session_factory

NOW = datetime(2026, 9, 20, tzinfo=timezone.utc)
PROVIDER = "twelvelabs"


def _auth(account_id):
    return AuthContext(account_id=account_id, session_id=uuid4(), request_id=uuid4(),
                       issued_at=datetime.now(timezone.utc))


# --- configuration: unset or partial means unregistered, never half-on -------------


def _pins(**overrides):
    values = dict(
        twelve_labs_api_key="k", twelve_labs_index_id="idx",
        twelve_labs_marengo_model_name="marengo3.0", twelve_labs_marengo_model_version="3.0",
        twelve_labs_pegasus_model_name="pegasus1.5", twelve_labs_pegasus_model_version="1.5")
    values.update(overrides)
    return ContentSettings(**values)


def test_video_analysis_is_configured_only_when_every_pin_is_present():
    assert twelve_labs_settings(_pins()) is not None


@pytest.mark.parametrize("missing", [
    "twelve_labs_api_key", "twelve_labs_index_id",
    "twelve_labs_marengo_model_name", "twelve_labs_marengo_model_version",
    "twelve_labs_pegasus_model_name", "twelve_labs_pegasus_model_version",
])
def test_one_missing_pin_leaves_video_analysis_unconfigured(missing):
    """M3-PIN-1: a pin is never inferred.

    A half-configured adapter would attribute evidence to a model that never
    ran, so an incomplete configuration is the same as no configuration.
    """

    assert twelve_labs_settings(_pins(**{missing: None})) is None


def test_search_options_are_parsed_from_the_configured_list():
    assert twelve_labs_settings(_pins(twelve_labs_search_options="visual, audio")).search_options == ("visual", "audio")


def test_an_unknown_search_option_is_refused_rather_than_dropped():
    with pytest.raises(Exception):
        twelve_labs_settings(_pins(twelve_labs_search_options="visual,telepathy"))


def test_discovery_stays_unregistered_without_a_key():
    assert build_discovery_provider(ContentSettings()) is None


def test_discovery_stays_unregistered_for_an_unknown_search_depth():
    """"advanced" costs more per search than "basic", so neither is guessed."""

    assert build_discovery_provider(ContentSettings(tavily_api_key="k", tavily_search_depth="deep")) is None


# --- migration 0012 matches the ORM ------------------------------------------------


def test_the_video_assets_migration_matches_the_declared_row():
    """Either side changing without the other fails here (0011's convention)."""

    import importlib.util
    from pathlib import Path

    path = Path(__file__).parents[2] / "migrations" / "versions" / "0012_m3_video_assets.py"
    source = path.read_text(encoding="utf-8")
    declared = {column.name for column in VideoAssetRow.__table__.columns}
    for name in declared:
        assert f'"{name}"' in source, f"migration 0012 does not create column {name}"
    assert source.count('op.create_table(') == 1
    assert "video_assets" in source
    # The 0006 gap is closed deliberately, and the retrofit is deliberately not
    # attempted: existing rows may have no asset.
    assert "video_evidence_candidates" not in source.split("def upgrade")[1]


# --- database-backed: authorization and the DERIVED evidence path -------------------

pytest_plugins: tuple[str, ...] = ()


@pytest.fixture
async def db_session():
    url = os.environ.get("NETRA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("NETRA_TEST_DATABASE_URL (a disposable local database) is not set")
    engine = create_engine(url)
    factory: async_sessionmaker[AsyncSession] = create_session_factory(engine)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _video(session, *, account_id, status="ready", is_active=True, kind=VideoSourceKind.YOUTUBE):
    """One account, source, ready version, video asset and one candidate."""

    source_id, version_id, video_id = uuid4(), uuid4(), uuid4()
    await session.execute(insert(SourceRow).values(
        source_id=source_id, account_id=account_id, title="Ohm's law lecture", created_at=NOW))
    await session.execute(insert(SourceVersionRow).values(
        source_version_id=version_id, source_id=source_id, version_number=1, status=status,
        is_active=is_active, created_at=NOW, object_key="k", content_hash="h" * 64))
    await session.execute(insert(VideoAssetRow).values(
        video_id=video_id, source_id=source_id, source_version_id=version_id, kind=kind.value,
        external_ref="dQw4w9WgXcQ", duration_ms=600000, created_at=NOW))
    candidate_id = uuid4()
    await session.execute(insert(VideoEvidenceCandidateRow).values(
        candidate_id=candidate_id, idempotency_key=str(uuid4()), ordinal=0, video_id=video_id,
        source_version_id=version_id, locator="00:01:30", start_ms=90000, end_ms=105000,
        kind=VideoEvidenceKind.VISUAL_DESCRIPTION.value,
        description="The plotted line rises steadily from the origin.",
        provenance={"provider": PROVIDER, "model_name": "pegasus1.5", "model_version": "1.5",
                    "produced_at": NOW.isoformat(), "stage": "derive_video_evidence"},
        created_at=NOW))
    await session.flush()
    return source_id, version_id, video_id, candidate_id


async def _cleanup(session, source_id):
    await session.execute(delete(SourceRow).where(SourceRow.source_id == source_id))
    await session.commit()


@pytest.mark.integration
async def test_a_video_asset_and_its_evidence_are_readable_by_their_owner(db_session):
    account = uuid4()
    source_id, version_id, video_id, candidate_id = await _video(db_session, account_id=account)
    try:
        store = AsyncPostgresVideoEvidenceStore(db_session)
        asset = await store.asset(_auth(account), video_id)
        assert asset is not None and asset.source_version_id == version_id
        assert asset.kind is VideoSourceKind.YOUTUBE and asset.external_ref == "dQw4w9WgXcQ"
        assert (await store.asset_for_version(_auth(account), version_id)).video_id == video_id
        items = await store.items(_auth(account), version_id)
        assert [item.video_evidence_id for item in items] == [candidate_id]
        item = items[0]
        assert item.reference.start_ms == 90000 and item.reference.video_id == video_id
        assert item.supports_visual_claim  # a visual description may back a visual claim
        assert item.provenance is not None and item.provenance.model_name == "pegasus1.5"
    finally:
        await _cleanup(db_session, source_id)


@pytest.mark.integration
async def test_another_accounts_video_is_absent_rather_than_denied(db_session):
    """These reads must not be usable to probe for other accounts' videos."""

    account = uuid4()
    source_id, version_id, video_id, _ = await _video(db_session, account_id=account)
    try:
        store = AsyncPostgresVideoEvidenceStore(db_session)
        stranger = _auth(uuid4())
        assert await store.asset(stranger, video_id) is None
        assert await store.asset_for_version(stranger, version_id) is None
        assert await store.items(stranger, version_id) == []
    finally:
        await _cleanup(db_session, source_id)


@pytest.mark.integration
async def test_video_evidence_resolves_as_derived_not_source_verified(db_session):
    """A provider model wrote the description; it is never the source's words."""

    account = uuid4()
    source_id, version_id, _, candidate_id = await _video(db_session, account_id=account)
    try:
        resolver = AsyncPostgresEvidenceResolver(db_session)
        [resolution] = await resolver.resolve(_auth(account), [str(candidate_id)],
                                              pinned_source_version_id=version_id)
        assert resolution.is_resolved
        assert resolution.evidence.trust is EvidenceTrust.DERIVED
        assert resolution.evidence.text.startswith("The plotted line")
        assert resolution.evidence.locator == "00:01:30"
        assert resolution.evidence.evidence_version == 1
    finally:
        await _cleanup(db_session, source_id)


@pytest.mark.integration
async def test_video_evidence_from_another_account_is_unauthorized(db_session):
    account = uuid4()
    source_id, _, _, candidate_id = await _video(db_session, account_id=account)
    try:
        resolver = AsyncPostgresEvidenceResolver(db_session)
        [resolution] = await resolver.resolve(_auth(uuid4()), [str(candidate_id)])
        assert not resolution.is_resolved
        assert resolution.rejection_reason is EvidenceRejectionReason.UNAUTHORIZED
    finally:
        await _cleanup(db_session, source_id)


@pytest.mark.integration
async def test_video_evidence_pinned_to_a_different_version_is_refused(db_session):
    account = uuid4()
    source_id, _, _, candidate_id = await _video(db_session, account_id=account)
    try:
        resolver = AsyncPostgresEvidenceResolver(db_session)
        [resolution] = await resolver.resolve(_auth(account), [str(candidate_id)],
                                              pinned_source_version_id=uuid4())
        assert resolution.rejection_reason is EvidenceRejectionReason.SOURCE_VERSION_MISMATCH
    finally:
        await _cleanup(db_session, source_id)


@pytest.mark.integration
async def test_video_evidence_of_an_unready_version_is_never_citable(db_session):
    account = uuid4()
    source_id, _, _, candidate_id = await _video(db_session, account_id=account, status="pending",
                                                 is_active=False)
    try:
        resolver = AsyncPostgresEvidenceResolver(db_session)
        [resolution] = await resolver.resolve(_auth(account), [str(candidate_id)])
        assert resolution.rejection_reason is EvidenceRejectionReason.SOURCE_VERSION_MISMATCH
    finally:
        await _cleanup(db_session, source_id)


@pytest.mark.integration
async def test_an_unknown_evidence_id_is_still_not_found(db_session):
    """The video fallback must not turn a miss into anything but NOT_FOUND."""

    resolver = AsyncPostgresEvidenceResolver(db_session)
    [resolution] = await resolver.resolve(_auth(uuid4()), [str(uuid4())])
    assert resolution.rejection_reason is EvidenceRejectionReason.NOT_FOUND


@pytest.mark.integration
async def test_analysis_is_not_started_until_a_provider_binding_exists(db_session):
    account = uuid4()
    source_id, _, video_id, _ = await _video(db_session, account_id=account)
    try:
        store = AsyncPostgresVideoEvidenceStore(db_session)
        asset = await store.asset(_auth(account), video_id)
        facts = PostgresCapabilityFacts(db_session, provider=PROVIDER, embedded_player_available=True)
        assert (await facts.analysis(_auth(account), asset)).stage is AnalysisStage.NOT_STARTED
        await db_session.execute(insert(VideoProviderBindingRow).values(
            video_id=video_id, provider=PROVIDER, provider_index_id="idx", provider_video_id="pv",
            model_name="marengo3.0", model_version="3.0", created_at=NOW))
        await db_session.flush()
        assert (await facts.analysis(_auth(account), asset)).stage is AnalysisStage.INDEXED
        binding = await store.binding(video_id, PROVIDER)
        assert binding is not None and binding.model_name == "marengo3.0"
    finally:
        await _cleanup(db_session, source_id)


@pytest.mark.integration
async def test_a_youtube_videos_embeddability_is_unknown_until_a_player_reports_it(db_session):
    """readiness.py keeps playback and analysis independent; unknown is not True."""

    account = uuid4()
    source_id, _, video_id, _ = await _video(db_session, account_id=account)
    try:
        store = AsyncPostgresVideoEvidenceStore(db_session)
        asset = await store.asset(_auth(account), video_id)
        facts = PostgresCapabilityFacts(db_session, provider=PROVIDER, embedded_player_available=True)
        playback = await facts.playback(_auth(account), asset)
        assert playback.authorized and playback.media_present
        assert playback.embeddable is None and playback.container_supported is None
    finally:
        await _cleanup(db_session, source_id)
