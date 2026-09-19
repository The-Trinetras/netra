import pytest

from bootstrap_real_golden import PARSED, PDF, SOURCE_ID, VERSION_ID, validate_locked_fixture


# The third-party source PDF and its parsed text are local-only by policy
# (.gitignore: the public repository must not redistribute them), so a clean
# checkout cannot run this check. Skip visibly rather than fail or pass.
@pytest.mark.skipif(
    not (PDF.is_file() and PARSED.is_file()),
    reason="local-only evaluation/fixtures documents are absent (not redistributable)",
)
def test_locked_real_fixture_reconstructs_all_golden_chunks():
    parsed, blocks, chunks, golden = validate_locked_fixture()

    assert parsed == 694
    assert blocks == 694
    assert chunks == 20
    assert len(golden) == 10


def test_historical_locked_identities_are_uuid4():
    # These identities predate the UUIDv5 SourceIngestionService and cannot be
    # reverse-engineered from an operation key.
    assert SOURCE_ID.version == 4
    assert VERSION_ID.version == 4
