"""D2 support eligibility, Marengo hit -> stored evidence selection, and media span keys."""

from __future__ import annotations

from uuid import uuid4


from fixtures.ohm_law import (
    SOURCE_VERSION_ID,
    VIDEO_ID,
    equation_source_check,
    extracted_equation,
    extracted_table,
    table_source_check,
)
from fixtures.provider_fakes import local_tracer
from netra_api.multimedia.equations.validation import EquationSourceCheck, validate_equation_against_source
from netra_api.multimedia.grounding import (
    ClaimKind,
    MediaEvidenceKind,
    SupportEligibility,
    support_eligibility,
    video_kind,
)
from netra_api.multimedia.providers.twelve_labs_client import MarengoHit
from netra_api.multimedia.tables.validation import validate_table_against_source
from netra_api.multimedia.tracing import PROPOSED_MEDIA_ATTRIBUTES, media_span, record_outcome
from netra_api.multimedia.video.models import VideoEvidenceItem, VideoEvidenceKind, VideoEvidenceReference
from netra_api.multimedia.video.search import select_evidence_for_hits
from netra_api.multimedia.validation import ValidationReport
from netra_api.platform import tracing as platform_tracing


# ---------------------------------------------------------------- D2 eligibility


def test_source_verified_table_may_support_a_value_claim():
    report = validate_table_against_source(extracted_table(), table_source_check())
    verdict = support_eligibility(MediaEvidenceKind.TABLE, ClaimKind.VALUE, report=report)
    assert verdict.may_support


def test_valid_citation_to_a_wrong_unit_table_is_not_support():
    report = validate_table_against_source(extracted_table(voltage_unit="mV"), table_source_check())
    verdict = support_eligibility(MediaEvidenceKind.TABLE, ClaimKind.VALUE, report=report)
    assert verdict.eligibility is SupportEligibility.UNVERIFIED_EXTRACTION and not verdict.may_support


def test_unchecked_and_unreadable_extractions_fail_closed():
    assert support_eligibility(MediaEvidenceKind.EQUATION, ClaimKind.VALUE).eligibility is SupportEligibility.UNVERIFIED_EXTRACTION
    assert support_eligibility(MediaEvidenceKind.EQUATION, ClaimKind.VALUE, report=ValidationReport(object_id="x")).eligibility is SupportEligibility.UNVERIFIED_EXTRACTION
    unreadable = EquationSourceCheck(equation_index=1, canonical_form="(V = (I × R))", unreadable_in_source=True)
    report = validate_equation_against_source(extracted_equation(), unreadable)
    assert support_eligibility(MediaEvidenceKind.EQUATION, ClaimKind.VALUE, report=report).eligibility is SupportEligibility.UNREADABLE


def test_transcript_supports_speech_but_never_what_was_shown():
    assert support_eligibility(MediaEvidenceKind.VIDEO_TRANSCRIPT, ClaimKind.SPOKEN).may_support
    for claim in (ClaimKind.VISUAL, ClaimKind.VALUE):
        verdict = support_eligibility(MediaEvidenceKind.VIDEO_TRANSCRIPT, claim)
        assert verdict.eligibility is SupportEligibility.CITATION_ONLY


def test_generated_visual_description_is_pending_the_m1_m3_rule():
    verdict = support_eligibility(video_kind(VideoEvidenceKind.VISUAL_DESCRIPTION), ClaimKind.VISUAL)
    assert verdict.eligibility is SupportEligibility.PENDING_DECISION and not verdict.may_support
    assert support_eligibility(video_kind(VideoEvidenceKind.SCENE_SUMMARY), ClaimKind.VISUAL).eligibility is SupportEligibility.CITATION_ONLY


def test_document_objects_never_support_spoken_claims():
    report = validate_equation_against_source(extracted_equation(), equation_source_check())
    assert support_eligibility(MediaEvidenceKind.EQUATION, ClaimKind.SPOKEN, report=report).eligibility is SupportEligibility.CITATION_ONLY


# ---------------------------------------------------------------- hit -> evidence


def _item(start, end, kind=VideoEvidenceKind.VISUAL_DESCRIPTION, evidence_id=None):
    return VideoEvidenceItem(
        video_evidence_id=evidence_id or uuid4(),
        reference=VideoEvidenceReference(
            evidence_id=f"ev-{start}", source_version_id=SOURCE_VERSION_ID, locator="lecture-v1", start_ms=start, end_ms=end, video_id=VIDEO_ID
        ),
        kind=kind,
        description="d",
    )


def test_hits_select_overlapping_stored_items_in_rank_order_without_duplicates():
    early, middle, late = _item(0, 30_000), _item(30_000, 60_000), _item(60_000, 90_000)
    transcript = _item(45_000, 50_000, VideoEvidenceKind.TRANSCRIPT_SEGMENT)
    hits = [MarengoHit(start_ms=46_000, end_ms=49_000, rank=1), MarengoHit(start_ms=5_000, end_ms=58_000, rank=2)]

    selected = select_evidence_for_hits([late, early, middle, transcript], hits, limit=10)
    assert selected == [middle, transcript, early]


def test_a_hit_over_unprocessed_time_selects_nothing_and_limit_is_respected():
    items = [_item(0, 10_000)]
    assert select_evidence_for_hits(items, [MarengoHit(start_ms=20_000, end_ms=30_000)], limit=5) == []
    assert select_evidence_for_hits(items * 1, [MarengoHit(start_ms=0, end_ms=5_000)], limit=0) == []


# ---------------------------------------------------------------- media span keys


def test_proposed_media_keys_are_not_emitted_until_m1_allowlists_them():
    tracer, exporter = local_tracer()
    with media_span(tracer, "media.video.moment", operation="evidence_at_player_time", video_id=VIDEO_ID, captured_time_ms=48_000, start_ms=38_000, end_ms=58_000, source_version_id=SOURCE_VERSION_ID) as span:
        record_outcome(span, "transcript_only", uncertainty="transcript_only")
    tracer.shutdown(1)
    (span,) = exporter.spans
    assert not any(key.startswith("netra.media.") for key in span.attributes)
    assert span.attributes["netra.gap"] == "transcript_only"
    assert tracer.diagnostics.attributes_dropped == 0


def test_approved_media_keys_switch_on_without_an_m3_change(monkeypatch):
    validators = {
        "netra.media.video_id": platform_tracing._is_id,
        "netra.media.start_ms": platform_tracing._is_int,
        "netra.media.end_ms": platform_tracing._is_int,
        "netra.media.captured_time_ms": platform_tracing._is_int,
        "netra.media.stage": platform_tracing._is_code,
        "netra.media.object_kind": platform_tracing._is_code,
        "netra.media.uncertainty": platform_tracing._is_code,
    }
    assert set(validators) == set(PROPOSED_MEDIA_ATTRIBUTES)
    for key, validator in validators.items():
        monkeypatch.setitem(platform_tracing.ALLOWED_ATTRIBUTES, key, validator)

    tracer, exporter = local_tracer()
    with media_span(tracer, "media.video.moment", operation="evidence_at_player_time", video_id=VIDEO_ID, captured_time_ms=48_000, start_ms=38_000, end_ms=58_000) as span:
        record_outcome(span, "transcript_only", uncertainty="transcript_only")
    tracer.shutdown(1)
    (span,) = exporter.spans
    assert span.attributes["netra.media.video_id"] == str(VIDEO_ID)
    assert (span.attributes["netra.media.start_ms"], span.attributes["netra.media.end_ms"]) == (38_000, 58_000)
    assert span.attributes["netra.media.captured_time_ms"] == 48_000
    assert span.attributes["netra.media.uncertainty"] == "transcript_only"
    assert tracer.diagnostics.attributes_dropped == 0


async def test_concurrent_media_spans_keep_separate_traces():
    import asyncio

    tracer, exporter = local_tracer()

    async def one(name):
        with media_span(tracer, name, operation="describe_video"):
            await asyncio.sleep(0.01)
            with media_span(tracer, name + ".child", operation="pegasus_describe_window"):
                await asyncio.sleep(0.01)

    await asyncio.gather(one("a"), one("b"))
    tracer.shutdown(1)
    by_name = {span.name: span for span in exporter.spans}
    assert by_name["a.child"].trace_id == by_name["a"].trace_id != by_name["b"].trace_id == by_name["b.child"].trace_id


def test_disabled_tracing_changes_nothing():
    with media_span(None, "media.x", operation="x") as span:
        record_outcome(span, "ok")
    assert span is platform_tracing.NOOP_SPAN
