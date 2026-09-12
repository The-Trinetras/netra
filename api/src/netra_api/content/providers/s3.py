"""Object-storage provider interface.

S3 stores bytes such as source files and generated audio (CLAUDE.md
"Data authority"). No S3 SDK call is implemented here — this only fixes
the boundary business logic depends on. No provider is wired up in
this scaffold.
"""

from __future__ import annotations

from typing import Protocol


class ObjectStorageProvider(Protocol):
    """Typed contract for reading/writing opaque object bytes by key."""

    async def put_object(self, key: str, data: bytes, content_type: str) -> None:
        ...

    async def get_object(self, key: str) -> bytes:
        ...

    async def generate_presigned_url(self, key: str, expires_in_seconds: int) -> str:
        ...
