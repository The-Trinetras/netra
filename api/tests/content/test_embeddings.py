from types import SimpleNamespace

import pytest

from netra_api.content.settings import ContentSettings
from netra_api.content.retrieval.embeddings import (
    EmbeddingConfigurationError,
    EmbeddingProviderError,
    GeminiEmbeddingProvider,
)


class _FakeModels:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []

    async def embed_content(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        if self.response is not None:
            return self.response
        return SimpleNamespace(embeddings=[SimpleNamespace(values=[1, 0]) for _ in kwargs["contents"]])


class _FakeClient:
    def __init__(self, models):
        self.aio = SimpleNamespace(models=models)


def _provider(response=None, **overrides):
    settings_values = {"gemini_api_key": "test-key", "gemini_embedding_dimension": 3, **overrides}
    settings = ContentSettings(**settings_values)
    models = _FakeModels(response=response)
    return GeminiEmbeddingProvider(settings, _FakeClient(models)), models


@pytest.mark.asyncio
async def test_single_embedding_contains_vector_and_specification():
    provider, models = _provider(SimpleNamespace(embeddings=[SimpleNamespace(values=[1, 2, 3])]))
    result = await provider.embed("text")
    assert result.vector == [1.0, 2.0, 3.0]
    assert result.specification.version == "gemini-embedding-001:3"
    assert models.calls[0]["model"] == "gemini-embedding-001"


@pytest.mark.asyncio
async def test_batch_preserves_order_and_batches_inputs():
    provider, models = _provider(gemini_embedding_dimension=2, gemini_embedding_batch_size=2)
    results = await provider.embed_batch(["a", "b", "c"])
    assert [result.vector for result in results] == [[1.0, 0.0], [1.0, 0.0], [1.0, 0.0]]
    assert [call["contents"] for call in models.calls] == [["a", "b"], ["c"]]


@pytest.mark.asyncio
async def test_empty_batch_does_not_call_provider():
    provider, models = _provider()
    assert await provider.embed_batch([]) == []
    assert models.calls == []


@pytest.mark.asyncio
async def test_missing_api_key_fails_without_exposing_credentials():
    provider = GeminiEmbeddingProvider(ContentSettings(gemini_api_key=None))
    with pytest.raises(EmbeddingConfigurationError, match="API key is not configured"):
        await provider.embed("text")


@pytest.mark.asyncio
async def test_count_mismatch_is_rejected():
    provider, _ = _provider(SimpleNamespace(embeddings=[]))
    with pytest.raises(EmbeddingProviderError, match="invalid embedding count"):
        await provider.embed("text")


@pytest.mark.asyncio
async def test_provider_error_is_translated_without_provider_payload():
    settings = ContentSettings(gemini_api_key="test-key", gemini_embedding_dimension=3)
    models = _FakeModels(error=RuntimeError("secret provider payload"))
    provider = GeminiEmbeddingProvider(settings, _FakeClient(models))
    with pytest.raises(EmbeddingProviderError, match="request failed") as error:
        await provider.embed("text")
    assert "secret provider payload" not in str(error.value)


def test_embedding_configuration_defaults():
    settings = ContentSettings()
    assert settings.gemini_embedding_model == "gemini-embedding-001"
    assert settings.gemini_embedding_dimension == 1536
