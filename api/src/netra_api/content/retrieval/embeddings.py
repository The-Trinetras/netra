"""Embedding provider contract and Gemini implementation.

Embeddings are derived projections. This module deliberately has no
PostgreSQL or Pinecone dependency; callers decide how to persist or project
the returned vector and specification.
"""

from __future__ import annotations

import math
import re
from typing import Any, Protocol, Sequence

from pydantic import BaseModel, Field

from netra_api.content.settings import ContentSettings


class EmbeddingError(RuntimeError):
    """Base class for safe, provider-independent embedding failures."""


class EmbeddingConfigurationError(EmbeddingError):
    """The adapter cannot call Gemini with the supplied configuration."""


class EmbeddingProviderError(EmbeddingError):
    """Gemini failed or returned an unusable response."""


class EmbeddingQuotaError(EmbeddingProviderError):
    """The embedding quota is exhausted for now (HTTP 429).

    Kept distinct from other provider failures because it is purely temporal:
    the identical request succeeds once the window resets, so a caller may
    wait rather than abandon the work. ``retry_after_seconds`` is what the
    provider itself asked for, or None when it did not say. This type asserts
    nothing about how long a caller should wait; that bound belongs to the
    caller.
    """

    def __init__(self, message: str = "embedding quota exhausted", retry_after_seconds: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


_NOT_QUOTA = object()
"""Sentinel: this exception is not a quota failure (None means "429, no delay given")."""

_RETRY_DELAY = re.compile(r"'retryDelay':\s*'(\d+(?:\.\d+)?)s'")


def _quota_retry_after(exc: Exception) -> Any:
    """Seconds the provider asked us to wait, None if it asked for none, or
    ``_NOT_QUOTA`` when this failure is not a 429 at all.

    Read from the exception rather than the SDK's typed errors so the adapter
    does not depend on which error classes a given google-genai version raises.
    """

    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    text = str(exc)
    if code != 429 and "RESOURCE_EXHAUSTED" not in text and "429" not in text:
        return _NOT_QUOTA
    match = _RETRY_DELAY.search(text)
    return float(match.group(1)) if match else None


class EmbeddingSpec(BaseModel):
    """Exact vector configuration used to create an embedding."""

    model: str = Field(min_length=1)
    dimension: int = Field(ge=1)
    version: str = Field(min_length=1)

    @classmethod
    def from_settings(cls, settings: ContentSettings) -> "EmbeddingSpec":
        model = settings.gemini_embedding_model
        dimension = settings.gemini_embedding_dimension
        return cls(model=model, dimension=dimension, version=f"{model}:{dimension}")


class EmbeddingResult(BaseModel):
    """One input's vector plus the specification that produced it."""

    vector: list[float]
    specification: EmbeddingSpec


class EmbeddingProvider(Protocol):
    async def embed(self, text: str) -> EmbeddingResult:
        ...

    async def embed_batch(self, texts: Sequence[str]) -> list[EmbeddingResult]:
        ...

    async def embed_query(self, text: str) -> list[float]:
        ...


class GeminiEmbeddingProvider:
    """Async adapter for the pinned ``google-genai`` client API."""

    def __init__(self, settings: ContentSettings | None = None, client: Any | None = None) -> None:
        self.settings = settings or ContentSettings()
        self.specification = EmbeddingSpec.from_settings(self.settings)
        self._client = client

    async def embed(self, text: str) -> EmbeddingResult:
        return (await self._embed_many([text], "RETRIEVAL_DOCUMENT"))[0]

    async def embed_batch(self, texts: Sequence[str]) -> list[EmbeddingResult]:
        if not texts:
            return []
        return await self._embed_many(texts, "RETRIEVAL_DOCUMENT")

    async def embed_query(self, text: str) -> list[float]:
        return (await self._embed_many([text], "RETRIEVAL_QUERY"))[0].vector

    async def _embed_many(self, texts: Sequence[str], task_type: str) -> list[EmbeddingResult]:
        validated = self._validate_inputs(texts)
        if not validated:
            return []
        batch_size = self.settings.gemini_embedding_batch_size
        if batch_size < 1:
            raise EmbeddingConfigurationError("Gemini embedding batch size must be positive")
        output: list[EmbeddingResult] = []
        for start in range(0, len(validated), batch_size):
            batch = validated[start : start + batch_size]
            response = await self._call_gemini(batch, task_type)
            output.extend(self._parse_response(response, len(batch)))
        return output

    @staticmethod
    def _validate_inputs(texts: Sequence[str]) -> list[str]:
        validated = list(texts)
        for text in validated:
            if not isinstance(text, str) or not text.strip():
                raise ValueError("embedding text must be a non-empty string")
        return validated

    async def _call_gemini(self, texts: list[str], task_type: str) -> Any:
        client = self._client or self._create_client()
        try:
            from google.genai import types

            request_config = types.EmbedContentConfig(
                task_type=task_type,
                output_dimensionality=self.specification.dimension,
            )
            return await client.aio.models.embed_content(
                model=self.specification.model,
                contents=texts,
                config=request_config,
            )
        except EmbeddingError:
            raise
        except Exception as exc:
            retry_after = _quota_retry_after(exc)
            if retry_after is not _NOT_QUOTA:
                # 429: temporal, not a bad request. The message is not carried
                # through - provider text is untrusted and may name resources.
                raise EmbeddingQuotaError(retry_after_seconds=retry_after) from exc
            raise EmbeddingProviderError("Gemini embedding request failed") from exc

    def _create_client(self) -> Any:
        if not self.settings.gemini_api_key:
            raise EmbeddingConfigurationError("Gemini API key is not configured")
        try:
            from google import genai
            self._client = genai.Client(api_key=self.settings.gemini_api_key)
            return self._client
        except Exception as exc:
            raise EmbeddingConfigurationError("Gemini client could not be initialized") from exc

    def _parse_response(self, response: Any, expected_count: int) -> list[EmbeddingResult]:
        embeddings = getattr(response, "embeddings", None)
        if not isinstance(embeddings, list) or len(embeddings) != expected_count:
            raise EmbeddingProviderError("Gemini returned an invalid embedding count")
        results: list[EmbeddingResult] = []
        for embedding in embeddings:
            values = getattr(embedding, "values", None)
            if not isinstance(values, list) or len(values) != self.specification.dimension:
                raise EmbeddingProviderError("Gemini returned an invalid embedding vector")
            if not all(isinstance(value, (int, float)) and math.isfinite(float(value)) for value in values):
                raise EmbeddingProviderError("Gemini returned invalid embedding values")
            results.append(EmbeddingResult(
                vector=[float(value) for value in values], specification=self.specification
            ))
        return results
