"""Pinecone adapter for the rebuildable semantic-search projection."""

from __future__ import annotations

import asyncio
import math
from typing import Any, Protocol, Sequence

from pydantic import BaseModel, Field

from netra_api.content.settings import ContentSettings


class VectorIndexError(RuntimeError):
    """Base class for safe vector-index failures."""


class VectorIndexConfigurationError(VectorIndexError):
    """Required Pinecone configuration is missing or invalid."""


class VectorIndexProviderError(VectorIndexError):
    """Pinecone failed or returned a malformed response."""


class VectorMatch(BaseModel):
    id: str = Field(min_length=1)
    score: float
    metadata: dict[str, Any] = Field(default_factory=dict)


Vector = tuple[str, list[float], dict[str, Any]]


class VectorIndexProvider(Protocol):
    async def upsert(self, namespace: str, vectors: Sequence[Vector]) -> None:
        ...

    async def query(self, namespace: str, embedding: list[float], top_k: int,
                    metadata_filter: dict[str, Any] | None = None) -> list[VectorMatch]:
        ...

    async def delete(self, namespace: str, ids: Sequence[str]) -> None:
        ...


class PineconeVectorIndex:
    """Non-blocking adapter around the synchronous Pinecone 9.x client."""

    def __init__(self, settings: ContentSettings | None = None, client: Any | None = None) -> None:
        self.settings = settings or ContentSettings()
        self._client = client
        self._index: Any | None = None

    async def upsert(self, namespace: str, vectors: Sequence[Vector]) -> None:
        batch = list(vectors)
        self._validate_vectors(batch)
        if not batch:
            return
        size = self.settings.pinecone_upsert_batch_size
        if size < 1:
            raise VectorIndexConfigurationError("Pinecone upsert batch size must be positive")
        for start in range(0, len(batch), size):
            payload = [{"id": vector_id, "values": values, "metadata": metadata}
                       for vector_id, values, metadata in batch[start : start + size]]
            try:
                await asyncio.to_thread(self._index_for_call().upsert, vectors=payload, namespace=namespace)
            except VectorIndexError:
                raise
            except Exception as exc:
                raise VectorIndexProviderError("Pinecone upsert failed") from exc

    async def delete(self, namespace: str, ids: Sequence[str]) -> None:
        if not ids:
            return
        try:
            await asyncio.to_thread(self._index_for_call().delete, ids=list(ids), namespace=namespace)
        except Exception as exc:
            raise VectorIndexProviderError("Pinecone delete failed") from exc

    async def query(self, namespace: str, embedding: list[float], top_k: int,
                    metadata_filter: dict[str, Any] | None = None) -> list[VectorMatch]:
        if top_k < 1:
            raise ValueError("top_k must be positive")
        if not embedding or not all(isinstance(value, (int, float)) and math.isfinite(float(value)) for value in embedding):
            raise ValueError("query embedding must contain finite numeric values")
        try:
            response = await asyncio.to_thread(
                self._index_for_call().query, vector=embedding, top_k=top_k,
                namespace=namespace, filter=metadata_filter, include_metadata=True,
            )
        except Exception as exc:
            raise VectorIndexProviderError("Pinecone query failed") from exc
        matches = response.get("matches", []) if isinstance(response, dict) else getattr(response, "matches", None)
        if not isinstance(matches, list):
            raise VectorIndexProviderError("Pinecone returned an invalid match list")
        parsed: list[VectorMatch] = []
        try:
            for match in matches:
                item = match if isinstance(match, dict) else {"id": match.id, "score": match.score,
                                                               "metadata": getattr(match, "metadata", {})}
                parsed.append(VectorMatch(id=item["id"], score=float(item["score"]), metadata=item.get("metadata") or {}))
        except (KeyError, TypeError, ValueError) as exc:
            raise VectorIndexProviderError("Pinecone returned a malformed match") from exc
        return sorted(parsed, key=lambda match: (-match.score, match.id))

    @staticmethod
    def _validate_vectors(vectors: Sequence[Vector]) -> None:
        for vector_id, values, metadata in vectors:
            if not vector_id or not isinstance(metadata, dict):
                raise ValueError("vector ID and metadata are required")
            if not values or not all(isinstance(value, (int, float)) and math.isfinite(float(value)) for value in values):
                raise ValueError("vectors must contain finite numeric values")

    def _index_for_call(self) -> Any:
        if self._index is not None:
            return self._index
        if self._client is None and (not self.settings.pinecone_api_key or not self.settings.pinecone_index_name):
            raise VectorIndexConfigurationError("Pinecone API key and index name are required")
        try:
            from pinecone import Pinecone
            self._client = self._client or Pinecone(api_key=self.settings.pinecone_api_key)
            self._index = self._client.Index(self.settings.pinecone_index_name)
            return self._index
        except VectorIndexError:
            raise
        except Exception as exc:
            raise VectorIndexConfigurationError("Pinecone client could not be initialized") from exc
