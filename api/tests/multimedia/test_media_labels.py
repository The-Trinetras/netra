"""Every M3 label handed to M4 is reproduced by M3's real validators.

This guards the label file against drift from the code; it does not make
the labels human gold. The file itself must keep saying so.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fixtures.ohm_law import (
    VIDEO_ID,
    chart_source_check,
    equation_source_check,
    extracted_chart,
    extracted_equation,
    extracted_table,
    table_source_check,
    transcript_evidence,
    visual_evidence,
)
from fixtures.provider_fakes import FakeGateway, settings
from netra_api.multimedia.equations.validation import validate_equation_against_source
from netra_api.multimedia.figures.validation import validate_chart_against_source
from netra_api.multimedia.grounding import ClaimKind, MediaEvidenceKind, support_eligibility
from netra_api.multimedia.providers.errors import ProviderError, error_code
from netra_api.multimedia.providers.twelve_labs_client import IndexedAssetState, PegasusDescriptionAdapter
from netra_api.multimedia.tables.validation import validate_table_against_source
from netra_api.multimedia.video.evidence_resolution import resolve_moment_evidence
from netra_api.multimedia.video.timestamps import window_around

LABELS = Path(__file__).parent / "fixtures" / "evaluation" / "m3_media_labels_v1.json"
DATA = json.loads(LABELS.read_text(encoding="utf-8"))


def test_label_file_is_honest_about_its_review_state():
    assert DATA["original_media_reviewed"] is False
    assert DATA["label_reviewer"] is None
    assert DATA["review_status"] == "synthetic_fixture_not_original_media_reviewed"
    assert DATA["alyx_suggestions_accepted"] == []
    ids = [case["case_id"] for case in DATA["cases"]]
    assert len(ids) == len(set(ids))
    assert {case["failure_class"] for case in DATA["cases"]} <= set(DATA["failure_classes"])


def _report(case):
    variant = case["variant"]
    if case["object"] == "chart":
        check = chart_source_check(unreadable_axes=variant.get("unreadable_axes", False))
        return validate_chart_against_source(extracted_chart(**variant), check)
    if case["object"] == "table":
        return validate_table_against_source(extracted_table(**variant), table_source_check())
    return validate_equation_against_source(extracted_equation(**variant), equation_source_check())


@pytest.mark.parametrize(
    "case", [c for c in DATA["cases"] if c["object"] in ("chart", "table", "equation")], ids=lambda c: c["case_id"]
)
def test_document_object_labels(case):
    report = _report(case)
    expected = case["expected"]
    assert report.is_source_verified is expected["source_verified"]
    if expected.get("unreadable"):
        assert report.unreadable
    for field in expected.get("mismatch_fields", []):
        assert any(f.field == field for f in report.mismatches), field
    kind = MediaEvidenceKind(case["object"])
    assert support_eligibility(kind, ClaimKind.VALUE, report=report).may_support is expected["source_verified"]


@pytest.mark.parametrize("case", [c for c in DATA["cases"] if c["object"] == "lecture_moment"], ids=lambda c: c["case_id"])
def test_lecture_moment_labels(case):
    variant = case["variant"]
    builders = {"transcript": transcript_evidence, "visual": visual_evidence}
    items = [builders[name]() for name in variant["items"]]
    window = window_around(variant["captured_player_time_ms"], before_ms=variant["window_before_ms"], after_ms=variant["window_after_ms"])
    moment = resolve_moment_evidence(items, window, video_id=VIDEO_ID)
    assert moment.sufficiency.value == case["expected"]["sufficiency"]
    if "visual_claim_eligibility" in case["expected"]:
        best = "video_visual_description" if moment.visual_items else "video_transcript"
        verdict = support_eligibility(MediaEvidenceKind(best), ClaimKind.VISUAL)
        assert verdict.eligibility.value == case["expected"]["visual_claim_eligibility"]


@pytest.mark.parametrize("case", [c for c in DATA["cases"] if c["object"] == "video_processing"], ids=lambda c: c["case_id"])
async def test_video_processing_labels(case):
    gateway = FakeGateway(state=IndexedAssetState("ia-1", case["variant"]["index_status"], duration_ms=90_000, asset_id="as-1"))
    from uuid import uuid4

    with pytest.raises(ProviderError) as caught:
        await PegasusDescriptionAdapter(gateway, settings()).describe(
            provider_video_id="ia-1", video_id=VIDEO_ID, source_version_id=uuid4(), locator="lecture-v1", timeout_seconds=5
        )
    assert error_code(caught.value) == case["expected"]["error_code"]
    assert caught.value.retryable is case["expected"]["retryable"]
