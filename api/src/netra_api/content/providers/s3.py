"""Object-storage provider interface.

S3 stores bytes such as source files and generated audio (CLAUDE.md
"Data authority"). No S3 SDK call is implemented here — this only fixes
the boundary business logic depends on. No provider is wired up in
this scaffold.
"""

from __future__ import annotations

import ntpath
from pathlib import Path, PurePosixPath
from typing import Any, Optional, Protocol

import asyncio

from netra_api.content.settings import ContentSettings


class ObjectStorageProvider(Protocol):
    """Typed contract for reading/writing opaque object bytes by key."""

    async def put_object(self, key: str, data: bytes, content_type: str) -> None:
        ...

    async def get_object(self, key: str) -> bytes:
        ...

    async def generate_presigned_url(self, key: str, expires_in_seconds: int) -> str:
        ...


class Boto3ObjectStorage:
    """Async application boundary around boto3's synchronous S3 client."""

    def __init__(self, settings: ContentSettings | None = None, client=None) -> None:
        self.settings = settings or ContentSettings()
        self._client = client

    def _get_client(self):
        if self._client is None:
            import boto3
            self._client = boto3.client("s3", region_name=self.settings.aws_region)
        if not self.settings.s3_bucket:
            raise ValueError("s3_bucket is not configured")
        return self._client

    async def get_object(self, key: str) -> bytes:
        response = await asyncio.to_thread(self._get_client().get_object,
                                            Bucket=self.settings.s3_bucket, Key=key)
        return await asyncio.to_thread(response["Body"].read)

    async def put_object(self, key: str, data: bytes, content_type: str) -> None:
        await asyncio.to_thread(self._get_client().put_object,
                                Bucket=self.settings.s3_bucket, Key=key,
                                Body=data, ContentType=content_type)

    async def generate_presigned_url(self, key: str, expires_in_seconds: int) -> str:
        return await asyncio.to_thread(self._get_client().generate_presigned_url,
                                       "get_object", Params={"Bucket": self.settings.s3_bucket, "Key": key},
                                       ExpiresIn=expires_in_seconds)


class LocalFixtureObjectStorage:
    """Development-only object storage rooted at an explicit local directory.

    Object keys are always relative to ``root``.  This adapter exists to run
    the normal ingestion pipeline against local fixtures; it is not selected
    by default and is not a production replacement for S3.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        if not self.root.is_dir():
            raise ValueError("local_fixture_root must be an existing directory")

    def _path_for_key(self, key: str) -> Path:
        if not key or "\x00" in key:
            raise ValueError("object key must be a non-empty relative key")
        # Treat Windows separators as separators even when tests run on a
        # POSIX host, so keys cannot use platform-specific traversal tricks.
        normalized = key.replace("\\", "/")
        posix_key = PurePosixPath(normalized)
        if posix_key.is_absolute() or ntpath.isabs(key) or any(
            part in ("", ".", "..") for part in posix_key.parts
        ):
            raise ValueError("object key must stay within local_fixture_root")
        candidate = (self.root / Path(*posix_key.parts)).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("object key must stay within local_fixture_root") from exc
        return candidate

    async def get_object(self, key: str) -> bytes:
        path = self._path_for_key(key)
        try:
            return await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError as exc:
            raise FileNotFoundError(f"local fixture object does not exist: {key}") from exc

    async def put_object(self, key: str, data: bytes, content_type: str) -> None:
        del content_type
        path = self._path_for_key(key)
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_bytes, data)

    async def generate_presigned_url(self, key: str, expires_in_seconds: int) -> str:
        del expires_in_seconds
        self._path_for_key(key)
        normalized = key.replace("\\", "/")
        return f"local://{normalized}"


def build_object_storage(settings: Any) -> Optional[ObjectStorageProvider]:
    """The configured private object store, or None when it cannot be built.

    Mirrors the worker's selection (netra_worker.main) so both processes read
    the same bytes from the same place. Returns None instead of raising when
    storage is unusable: the API must still start and serve reading, and the
    routes that need storage report themselves unregistered rather than
    failing at import.
    """

    provider = getattr(settings, "storage_provider", None)
    try:
        if provider == "local_fixture":
            root = getattr(settings, "local_fixture_root", None)
            if not root:
                return None
            return LocalFixtureObjectStorage(root)
        if provider == "s3":
            if not getattr(settings, "s3_bucket", None):
                return None
            return Boto3ObjectStorage(settings)
    except Exception:  # noqa: BLE001 - an unusable store stays unregistered
        return None
    return None
