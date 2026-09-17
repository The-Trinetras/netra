from pathlib import Path
import inspect

import pytest

from netra_api.content.providers.s3 import LocalFixtureObjectStorage, ObjectStorageProvider


@pytest.fixture
def storage_root(tmp_path: Path) -> Path:
    (tmp_path / "documents").mkdir()
    (tmp_path / "documents" / "fixture.pdf").write_bytes(b"fixture bytes")
    return tmp_path


@pytest.mark.asyncio
async def test_local_fixture_storage_reads_object_key(storage_root: Path):
    storage = LocalFixtureObjectStorage(storage_root)

    assert await storage.get_object("documents/fixture.pdf") == b"fixture bytes"
    assert isinstance(storage, LocalFixtureObjectStorage)
    for method in ("get_object", "put_object", "generate_presigned_url"):
        assert hasattr(storage, method)
    # Keep the contract explicit without requiring runtime Protocol support.
    assert set(inspect.signature(ObjectStorageProvider.get_object).parameters) == {"self", "key"}
    assert set(inspect.signature(ObjectStorageProvider.put_object).parameters) == {
        "self", "key", "data", "content_type"
    }


@pytest.mark.asyncio
async def test_local_fixture_storage_writes_with_object_key(storage_root: Path):
    storage = LocalFixtureObjectStorage(storage_root)

    await storage.put_object("_netra/parsed/version.json", b"{}", "application/json")

    assert (storage_root / "_netra" / "parsed" / "version.json").read_bytes() == b"{}"
    assert await storage.generate_presigned_url("documents/fixture.pdf", 60) == "local://documents/fixture.pdf"


@pytest.mark.asyncio
async def test_local_fixture_storage_rejects_missing_and_traversal(storage_root: Path):
    storage = LocalFixtureObjectStorage(storage_root)

    with pytest.raises(FileNotFoundError, match="does not exist"):
        await storage.get_object("documents/missing.pdf")
    for key in ("../outside", "documents/../../outside", r"..\outside", "/etc/passwd", r"C:\\outside"):
        with pytest.raises(ValueError, match="within local_fixture_root"):
            await storage.get_object(key)


def test_local_fixture_storage_requires_existing_root(tmp_path: Path):
    with pytest.raises(ValueError, match="existing directory"):
        LocalFixtureObjectStorage(tmp_path / "missing")
