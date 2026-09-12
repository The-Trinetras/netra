from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from netra_api.content.sources.models import Source, SourceVersion, SourceVersionStatus


def test_source_version_defaults_to_pending_and_inactive():
    version = SourceVersion(
        source_version_id=uuid4(),
        source_id=uuid4(),
        version_number=1,
        created_at=datetime.now(timezone.utc),
    )
    assert version.status == SourceVersionStatus.PENDING
    assert version.is_active is False
    assert version.activated_at is None


def test_source_version_rejects_non_positive_version_number():
    with pytest.raises(ValidationError):
        SourceVersion(
            source_version_id=uuid4(),
            source_id=uuid4(),
            version_number=0,
            created_at=datetime.now(timezone.utc),
        )


def test_source_round_trips_through_model_dump():
    source = Source(
        source_id=uuid4(),
        account_id=uuid4(),
        title="Intro to Biology",
        created_at=datetime.now(timezone.utc),
    )
    assert Source.model_validate(source.model_dump()) == source
