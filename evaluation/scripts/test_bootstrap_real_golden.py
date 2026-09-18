from bootstrap_real_golden import SOURCE_ID, VERSION_ID, validate_locked_fixture


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
