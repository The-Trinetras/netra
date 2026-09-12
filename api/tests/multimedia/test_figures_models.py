from uuid import uuid4

import pytest
from pydantic import ValidationError

from netra_api.multimedia.figures.evidence import FigureEvidenceReference
from netra_api.multimedia.figures.models import FigureDescription, FigureRegion


def _reference(**overrides):
    defaults = dict(evidence_id="ev-1", source_version_id=uuid4(), locator="figure-1", figure_index=0)
    defaults.update(overrides)
    return FigureEvidenceReference(**defaults)


def test_figure_description_defaults_to_no_regions():
    description = FigureDescription(
        figure_id=uuid4(),
        reference=_reference(),
        short_label="Rainfall bar chart",
        long_description="A bar chart showing monthly rainfall totals.",
    )
    assert description.regions == []


def test_figure_description_round_trips_through_model_dump():
    description = FigureDescription(
        figure_id=uuid4(),
        reference=_reference(),
        short_label="Rainfall bar chart",
        long_description="A bar chart showing monthly rainfall totals.",
        regions=[FigureRegion(ordinal=0, label="x-axis", description="Months of the year")],
    )
    assert FigureDescription.model_validate(description.model_dump()) == description


def test_figure_region_requires_non_negative_ordinal():
    with pytest.raises(ValidationError):
        FigureRegion(ordinal=-1, description="The x-axis")


def test_figure_evidence_reference_requires_non_negative_figure_index():
    with pytest.raises(ValidationError):
        _reference(figure_index=-1)
